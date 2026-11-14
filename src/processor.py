import calendar
import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.database import create_database, get_connection


def fmt_money(val: float) -> str:
    val = round(val + 1e-9, 2) if val >= 0 else round(val - 1e-9, 2)
    if abs(val) < 0.005:
        val = 0.0
    return f"{val:.2f}"


def parse_ts(ts_str: str) -> datetime:
    return datetime.strptime(ts_str.strip(), "%Y-%m-%d %H:%M:%S")


def add_months(dt: datetime, months: int) -> datetime:
    year = dt.year + (dt.month - 1 + months) // 12
    month = (dt.month - 1 + months) % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return dt.replace(year=year, month=month, day=min(dt.day, last_day))


def month_last_day(ym: str) -> str:
    y, m = map(int, ym.split("-"))
    last = calendar.monthrange(y, m)[1]
    return f"{y:04d}-{m:02d}-{last:02d}"


class LedgerEngine:
    def __init__(self, db_path: str, output_dir: str):
        self.db_path = db_path
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.conn = get_connection(db_path)

        self.decisions: List[Dict[str, str]] = []
        self.statements: List[Dict[str, str]] = []

        self.month_flows: Dict[str, Dict[str, float]] = {}
        self.last_closing_liability: float = 0.0

    def _flow(self, ym: str) -> Dict[str, float]:
        if ym not in self.month_flows:
            self.month_flows[ym] = {
                "loads": 0.0,
                "redemptions": 0.0,
                "reversals": 0.0,
                "expiries": 0.0,
                "chargebacks": 0.0,
            }
        return self.month_flows[ym]

    def load_masters(self, stores_csv: str, wallets_csv: str) -> None:
        with self.conn:
            with open(stores_csv, mode="r", encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    self.conn.execute(
                        "INSERT INTO stores (store_id, store_name, city) VALUES (?, ?, ?)",
                        (row["store_id"].strip(), row["store_name"].strip(), row["city"].strip()),
                    )
            with open(wallets_csv, mode="r", encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    self.conn.execute(
                        """INSERT INTO wallets (wallet_id, customer_name, mobile, kyc_tier, opened_date, is_blocked)
                           VALUES (?, ?, ?, ?, ?, 0)""",
                        (
                            row["wallet_id"].strip(),
                            row["customer_name"].strip(),
                            row["mobile"].strip(),
                            row["kyc_tier"].strip(),
                            row["opened_date"].strip(),
                        ),
                    )

    def _record_decision(
        self,
        line_no: int,
        command: str,
        ts: str,
        entity_id: str,
        txn_or_ref: str,
        amt_req: float,
        amt_app: float,
        result: str,
        reason: str,
    ) -> None:
        self.decisions.append(
            {
                "line_no": str(line_no),
                "command": command,
                "ts": ts,
                "id": entity_id,
                "txn_or_ref": txn_or_ref,
                "amount_requested": fmt_money(amt_req),
                "amount_applied": fmt_money(amt_app),
                "result": result,
                "reason_code": reason,
            }
        )

    def _active_balance(self, owner_id: str, on_date: Optional[str] = None) -> float:
        if on_date:
            row = self.conn.execute(
                """SELECT COALESCE(SUM(remaining_amount), 0.0) AS bal
                   FROM lots
                   WHERE owner_id = ? AND status = 'ACTIVE' AND expiry_date >= ?""",
                (owner_id, on_date),
            ).fetchone()
        else:
            row = self.conn.execute(
                """SELECT COALESCE(SUM(remaining_amount), 0.0) AS bal
                   FROM lots
                   WHERE owner_id = ? AND status = 'ACTIVE'""",
                (owner_id,),
            ).fetchone()
        return round(float(row["bal"]), 2)

    def _all_active_liability(self) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(remaining_amount), 0.0) AS bal FROM lots WHERE status = 'ACTIVE'"
        ).fetchone()
        return round(float(row["bal"]), 2)

    def _log_ledger(
        self,
        owner_id: str,
        entry_ts: str,
        entry_type: str,
        lot_ref: str,
        lot_expiry: str,
        debit: float,
        credit: float,
        lot_bal_after: float,
    ) -> None:
        total_bal = self._active_balance(owner_id)
        self.conn.execute(
            """INSERT INTO ledger_entries
               (owner_id, entry_ts, entry_type, lot_ref, lot_expiry, debit, credit, lot_balance_after, total_balance_after)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                owner_id,
                entry_ts,
                entry_type,
                lot_ref,
                lot_expiry,
                round(debit, 2),
                round(credit, 2),
                round(lot_bal_after, 2),
                total_bal,
            ),
        )

    def _expire_due_lots(self, cutoff_date: str, event_ts: str) -> None:
        rows = self.conn.execute(
            """SELECT lot_ref, owner_id, expiry_date, remaining_amount
               FROM lots
               WHERE status = 'ACTIVE' AND expiry_date < ?""",
            (cutoff_date,),
        ).fetchall()
        for r in rows:
            rem = round(float(r["remaining_amount"]), 2)
            self.conn.execute(
                """UPDATE lots
                   SET status = 'EXPIRED', expired_date = ?
                   WHERE lot_ref = ?""",
                (r["expiry_date"], r["lot_ref"]),
            )
            if rem > 0:
                ym = r["expiry_date"][:7]
                self._flow(ym)["expiries"] = round(self._flow(ym)["expiries"] + rem, 2)
                self._log_ledger(
                    r["owner_id"],
                    f"{r['expiry_date']} 23:59:59",
                    "EXPIRE",
                    r["lot_ref"],
                    r["expiry_date"],
                    rem,
                    0.0,
                    0.0,
                )

    def handle_issue(self, line_no: int, parts: List[str]) -> None:
        _, ts, card_id, card_type, val_str, channel, pin, tender = [p.strip() for p in parts]
        val = round(float(val_str), 2)

        if val < 100.0 or val > 10000.0 or round(val % 50.0, 2) != 0.0:
            return

        exists = self.conn.execute("SELECT 1 FROM gift_cards WHERE card_id = ?", (card_id,)).fetchone()
        if exists:
            return

        with self.conn:
            self.conn.execute(
                """INSERT INTO gift_cards
                   (card_id, issue_ts, card_type, initial_value, channel, pin, buyer_tender, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'INACTIVE')""",
                (card_id, ts, card_type, val, channel, pin, tender),
            )
            self.conn.execute(
                """INSERT INTO lots
                   (lot_ref, owner_type, owner_id, lot_type, source, created_ts, expiry_date, original_amount, remaining_amount, status)
                   VALUES (?, 'CARD', ?, 'CARD', ?, ?, '9999-12-31', ?, ?, 'INACTIVE')""",
                (card_id, card_id, tender, ts, val, val),
            )

    def handle_activate(self, line_no: int, parts: List[str]) -> None:
        _, ts, card_id = [p.strip() for p in parts]
        card = self.conn.execute("SELECT * FROM gift_cards WHERE card_id = ?", (card_id,)).fetchone()
        if not card or card["status"] != "INACTIVE":
            return

        issue_dt = parse_ts(card["issue_ts"])
        act_dt = parse_ts(ts)
        days_elapsed = (act_dt.date() - issue_dt.date()).days

        with self.conn:
            if days_elapsed > 180:
                self.conn.execute("UPDATE gift_cards SET status = 'VOIDED' WHERE card_id = ?", (card_id,))
                self.conn.execute("UPDATE lots SET status = 'CLOSED', remaining_amount = 0.0 WHERE lot_ref = ?", (card_id,))
                return

            expiry_dt = act_dt + timedelta(days=365)
            expiry_str = expiry_dt.strftime("%Y-%m-%d")
            val = float(card["initial_value"])

            self.conn.execute(
                """UPDATE gift_cards
                   SET status = 'ACTIVE', activated_ts = ?, expiry_date = ?
                   WHERE card_id = ?""",
                (ts, expiry_str, card_id),
            )
            self.conn.execute(
                """UPDATE lots
                   SET status = 'ACTIVE', expiry_date = ?
                   WHERE lot_ref = ?""",
                (expiry_str, card_id),
            )
            ym = ts[:7]
            self._flow(ym)["loads"] = round(self._flow(ym)["loads"] + val, 2)
            self._log_ledger(card_id, ts, "ACTIVATE", card_id, expiry_str, 0.0, val, val)

    def handle_load_wallet(self, line_no: int, parts: List[str]) -> None:
        _, ts, wallet_id, load_ref, amt_str, source, load_type = [p.strip() for p in parts]
        dt = parse_ts(ts)
        date_str = dt.strftime("%Y-%m-%d")
        ym = ts[:7]
        self._expire_due_lots(date_str, ts)

        wallet = self.conn.execute("SELECT * FROM wallets WHERE wallet_id = ?", (wallet_id,)).fetchone()
        if not wallet:
            self._record_decision(line_no, "LOAD_WALLET", ts, wallet_id, load_ref, 0.0, 0.0, "DECLINED", "INVALID_WALLET")
            return

        neg_row = self.conn.execute(
            """SELECT COALESCE(SUM(remaining_amount), 0.0) AS neg
               FROM lots WHERE owner_id = ? AND remaining_amount < 0""",
            (wallet_id,),
        ).fetchone()
        neg_bal = round(float(neg_row["neg"]), 2)
        net_bal = self._active_balance(wallet_id, date_str)

        if amt_str == "BALANCE_TO_10000":
            amt = max(0.0, round(10000.0 - max(0.0, net_bal), 2))
        else:
            amt = round(float(amt_str), 2)

        if amt <= 0:
            self._record_decision(line_no, "LOAD_WALLET", ts, wallet_id, load_ref, amt, 0.0, "DECLINED", "INVALID_AMOUNT")
            return

        if self.conn.execute("SELECT 1 FROM lots WHERE lot_ref = ?", (load_ref,)).fetchone():
            self._record_decision(line_no, "LOAD_WALLET", ts, wallet_id, load_ref, amt, 0.0, "DECLINED", "DUPLICATE_REF")
            return

        kyc = wallet["kyc_tier"]
        if kyc == "MIN_KYC":
            if round(net_bal + amt, 2) > 10000.0:
                self._record_decision(line_no, "LOAD_WALLET", ts, wallet_id, load_ref, amt, 0.0, "DECLINED", "HOLDING_LIMIT")
                return
            month_loaded_row = self.conn.execute(
                """SELECT COALESCE(SUM(original_amount), 0.0) AS mload
                   FROM lots
                   WHERE owner_id = ? AND substr(created_ts, 1, 7) = ? AND lot_type IN ('PAID', 'PROMO')""",
                (wallet_id, ym),
            ).fetchone()
            if round(float(month_loaded_row["mload"]) + amt, 2) > 10000.0:
                self._record_decision(line_no, "LOAD_WALLET", ts, wallet_id, load_ref, amt, 0.0, "DECLINED", "MONTHLY_LOAD_LIMIT")
                return
        else:
            if round(net_bal + amt, 2) > 200000.0:
                self._record_decision(line_no, "LOAD_WALLET", ts, wallet_id, load_ref, amt, 0.0, "DECLINED", "HOLDING_LIMIT")
                return

        expiry_dt = dt + timedelta(days=90 if load_type == "PROMO" else 365)
        expiry_str = expiry_dt.strftime("%Y-%m-%d")

        rem_after_debt = amt
        if neg_bal < 0:
            neg_lots = self.conn.execute(
                "SELECT lot_ref, remaining_amount FROM lots WHERE owner_id = ? AND remaining_amount < 0 ORDER BY created_ts",
                (wallet_id,),
            ).fetchall()
            for nl in neg_lots:
                debt = abs(float(nl["remaining_amount"]))
                offset = min(rem_after_debt, debt)
                new_neg = round(float(nl["remaining_amount"]) + offset, 2)
                new_status = "CHARGED_BACK" if new_neg == 0 else "ACTIVE"
                self.conn.execute(
                    "UPDATE lots SET remaining_amount = ?, status = ? WHERE lot_ref = ?",
                    (new_neg, new_status, nl["lot_ref"]),
                )
                rem_after_debt = round(rem_after_debt - offset, 2)
                if rem_after_debt <= 0:
                    break

        with self.conn:
            self.conn.execute(
                """INSERT INTO lots
                   (lot_ref, owner_type, owner_id, lot_type, source, created_ts, expiry_date, original_amount, remaining_amount, status)
                   VALUES (?, 'WALLET', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    load_ref,
                    wallet_id,
                    load_type,
                    source,
                    ts,
                    expiry_str,
                    amt,
                    rem_after_debt,
                    "ACTIVE" if rem_after_debt > 0 else "CLOSED",
                ),
            )
            still_neg = self.conn.execute(
                "SELECT 1 FROM lots WHERE owner_id = ? AND remaining_amount < 0", (wallet_id,)
            ).fetchone()
            if not still_neg:
                self.conn.execute("UPDATE wallets SET is_blocked = 0 WHERE wallet_id = ?", (wallet_id,))

            self._flow(ym)["loads"] = round(self._flow(ym)["loads"] + amt, 2)
            self._log_ledger(wallet_id, ts, "LOAD", load_ref, expiry_str, 0.0, amt, rem_after_debt)
            self._record_decision(line_no, "LOAD_WALLET", ts, wallet_id, load_ref, amt, amt, "APPROVED", "OK")

    def handle_redeem(self, line_no: int, parts: List[str]) -> None:
        _, ts, txn_id, target_type, target_id, channel, loc_id, bill_str, restr_str, req_str, pin = [
            p.strip() for p in parts
        ]
        dt = parse_ts(ts)
        date_str = dt.strftime("%Y-%m-%d")
        ym = ts[:7]
        bill_total = round(float(bill_str), 2)
        restricted_amt = round(float(restr_str), 2)
        amt_req = round(float(req_str), 2)

        self._expire_due_lots(date_str, ts)

        if self.conn.execute("SELECT 1 FROM redemptions WHERE txn_id = ?", (txn_id,)).fetchone():
            self._record_decision(line_no, "REDEEM", ts, target_id, txn_id, amt_req, 0.0, "DECLINED", "DUPLICATE_TXN")
            return

        if target_type == "CARD":
            card = self.conn.execute("SELECT * FROM gift_cards WHERE card_id = ?", (target_id,)).fetchone()
            if not card:
                self._record_decision(line_no, "REDEEM", ts, target_id, txn_id, amt_req, 0.0, "DECLINED", "INVALID_CARD")
                return
            if card["status"] == "FROZEN":
                self._record_decision(line_no, "REDEEM", ts, target_id, txn_id, amt_req, 0.0, "DECLINED", "CARD_FROZEN")
                return
            if card["status"] == "INACTIVE":
                self._record_decision(line_no, "REDEEM", ts, target_id, txn_id, amt_req, 0.0, "DECLINED", "NOT_ACTIVATED")
                return
            if card["status"] != "ACTIVE":
                self._record_decision(line_no, "REDEEM", ts, target_id, txn_id, amt_req, 0.0, "DECLINED", "CARD_INACTIVE")
                return
            if channel == "ONLINE":
                if pin in ("", "-"):
                    self._record_decision(line_no, "REDEEM", ts, target_id, txn_id, amt_req, 0.0, "DECLINED", "PIN_REQUIRED")
                    return
                if pin != card["pin"]:
                    self._record_decision(line_no, "REDEEM", ts, target_id, txn_id, amt_req, 0.0, "DECLINED", "INVALID_PIN")
                    return

            cnt_row = self.conn.execute(
                """SELECT COUNT(*) AS cnt FROM redemptions
                   WHERE owner_id = ? AND substr(ts, 1, 10) = ? AND amount_applied > 0""",
                (target_id, date_str),
            ).fetchone()
            if int(cnt_row["cnt"]) >= 5:
                self._record_decision(line_no, "REDEEM", ts, target_id, txn_id, amt_req, 0.0, "DECLINED", "VELOCITY")
                return

        elif target_type == "WALLET":
            wallet = self.conn.execute("SELECT * FROM wallets WHERE wallet_id = ?", (target_id,)).fetchone()
            if not wallet:
                self._record_decision(line_no, "REDEEM", ts, target_id, txn_id, amt_req, 0.0, "DECLINED", "INVALID_WALLET")
                return
            if int(wallet["is_blocked"]) == 1:
                self._record_decision(line_no, "REDEEM", ts, target_id, txn_id, amt_req, 0.0, "DECLINED", "WALLET_BLOCKED")
                return

            spent_row = self.conn.execute(
                """SELECT COALESCE(SUM(amount_applied), 0.0) AS spent
                   FROM redemptions
                   WHERE owner_id = ? AND substr(ts, 1, 10) = ? AND reversed = 0""",
                (target_id, date_str),
            ).fetchone()
            if wallet["kyc_tier"] == "FULL_KYC" and round(float(spent_row["spent"]) + amt_req, 2) > 50000.0:
                self._record_decision(line_no, "REDEEM", ts, target_id, txn_id, amt_req, 0.0, "DECLINED", "VELOCITY")
                return

        max_allowed = amt_req
        partial_reason = "OK"
        if restricted_amt > 0:
            cap_70 = round(0.70 * bill_total, 2)
            if max_allowed > cap_70:
                max_allowed = cap_70
                partial_reason = "RESTRICTED_CAP_70"

        avail = self._active_balance(target_id, date_str)
        if avail <= 0:
            self._record_decision(line_no, "REDEEM", ts, target_id, txn_id, amt_req, 0.0, "DECLINED", "INSUFFICIENT_FUNDS")
            return

        waiver_amt = 0.0
        if avail < max_allowed:
            if target_type == "CARD":
                card = self.conn.execute("SELECT waiver_used FROM gift_cards WHERE card_id = ?", (target_id,)).fetchone()
                if avail < 10.0 and int(card["waiver_used"]) == 0:
                    waiver_amt = round(max_allowed - avail, 2)
                    max_allowed = avail
                    partial_reason = "SMALL_BALANCE_WAIVER"
                else:
                    self._record_decision(
                        line_no, "REDEEM", ts, target_id, txn_id, amt_req, 0.0, "DECLINED", "INSUFFICIENT_FUNDS"
                    )
                    return
            else:
                self._record_decision(
                    line_no, "REDEEM", ts, target_id, txn_id, amt_req, 0.0, "DECLINED", "INSUFFICIENT_FUNDS"
                )
                return

        lots = self.conn.execute(
            """SELECT lot_ref, lot_type, expiry_date, remaining_amount
               FROM lots
               WHERE owner_id = ? AND status = 'ACTIVE' AND expiry_date >= ? AND remaining_amount > 0
               ORDER BY CASE WHEN lot_type = 'PROMO' THEN 0 ELSE 1 END, expiry_date ASC, created_ts ASC""",
            (target_id, date_str),
        ).fetchall()

        to_deduct = max_allowed
        allocations: List[Tuple[str, str, float, float]] = []
        for l in lots:
            if to_deduct <= 0:
                break
            l_rem = round(float(l["remaining_amount"]), 2)
            take = min(l_rem, to_deduct)
            new_rem = round(l_rem - take, 2)
            allocations.append((l["lot_ref"], l["expiry_date"], take, new_rem))
            to_deduct = round(to_deduct - take, 2)

        with self.conn:
            for lot_ref, lot_exp, take, new_rem in allocations:
                new_status = "CLOSED" if (new_rem == 0 and target_type == "CARD") else "ACTIVE"
                self.conn.execute(
                    "UPDATE lots SET remaining_amount = ?, status = ? WHERE lot_ref = ?",
                    (new_rem, new_status, lot_ref),
                )
                self._log_ledger(target_id, ts, "REDEEM", lot_ref, lot_exp, take, 0.0, new_rem)

            if waiver_amt > 0 and target_type == "CARD":
                self.conn.execute(
                    "UPDATE gift_cards SET status = 'CLOSED', waiver_used = 1 WHERE card_id = ?",
                    (target_id,),
                )
            elif target_type == "CARD" and self._active_balance(target_id) == 0:
                self.conn.execute("UPDATE gift_cards SET status = 'CLOSED' WHERE card_id = ?", (target_id,))

            self.conn.execute(
                """INSERT INTO redemptions
                   (txn_id, ts, owner_type, owner_id, channel, location_id, bill_total, restricted_amount, amount_requested, amount_applied, waiver_amount, reversed)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (
                    txn_id,
                    ts,
                    target_type,
                    target_id,
                    channel,
                    loc_id,
                    bill_total,
                    restricted_amt,
                    amt_req,
                    max_allowed,
                    waiver_amt,
                ),
            )
            for lot_ref, _, take, _ in allocations:
                self.conn.execute(
                    "INSERT INTO redemption_allocations (txn_id, lot_ref, amount) VALUES (?, ?, ?)",
                    (txn_id, lot_ref, take),
                )

            self._flow(ym)["redemptions"] = round(self._flow(ym)["redemptions"] + max_allowed, 2)
            res_label = "PARTIAL" if max_allowed < amt_req else "APPROVED"
            self._record_decision(line_no, "REDEEM", ts, target_id, txn_id, amt_req, max_allowed, res_label, partial_reason)

    def handle_reverse(self, line_no: int, parts: List[str]) -> None:
        _, ts, orig_txn_id, rev_id = [p.strip() for p in parts]
        dt = parse_ts(ts)
        date_str = dt.strftime("%Y-%m-%d")
        ym = ts[:7]
        self._expire_due_lots(date_str, ts)

        orig = self.conn.execute("SELECT * FROM redemptions WHERE txn_id = ?", (orig_txn_id,)).fetchone()
        if not orig or int(orig["reversed"]) == 1:
            self._record_decision(line_no, "REVERSE", ts, orig_txn_id, rev_id, 0.0, 0.0, "DECLINED", "INVALID_OR_REVERSED")
            return

        allocs = self.conn.execute(
            "SELECT lot_ref, amount FROM redemption_allocations WHERE txn_id = ?", (orig_txn_id,)
        ).fetchall()
        same_day = orig["ts"][:10] == date_str
        owner_id = orig["owner_id"]
        owner_type = orig["owner_type"]
        total_restored = 0.0

        with self.conn:
            for idx, al in enumerate(allocs, start=1):
                lot = self.conn.execute("SELECT * FROM lots WHERE lot_ref = ?", (al["lot_ref"],)).fetchone()
                amt = round(float(al["amount"]), 2)
                lot_exp = lot["expiry_date"]

                if lot_exp < date_str:
                    continue

                if same_day:
                    new_rem = round(float(lot["remaining_amount"]) + amt, 2)
                    self.conn.execute(
                        "UPDATE lots SET remaining_amount = ?, status = 'ACTIVE' WHERE lot_ref = ?",
                        (new_rem, lot["lot_ref"]),
                    )
                    if owner_type == "CARD":
                        self.conn.execute("UPDATE gift_cards SET status = 'ACTIVE' WHERE card_id = ?", (owner_id,))
                    self._log_ledger(owner_id, ts, "REVERSE", lot["lot_ref"], lot_exp, 0.0, amt, new_rem)
                else:
                    new_lot_ref = f"{rev_id}-{idx}" if len(allocs) > 1 else rev_id
                    self.conn.execute(
                        """INSERT INTO lots
                           (lot_ref, owner_type, owner_id, lot_type, source, created_ts, expiry_date, original_amount, remaining_amount, status)
                           VALUES (?, ?, ?, 'REVERSAL', ?, ?, ?, ?, ?, 'ACTIVE')""",
                        (new_lot_ref, owner_type, owner_id, orig_txn_id, ts, lot_exp, amt, amt),
                    )
                    if owner_type == "CARD":
                        self.conn.execute("UPDATE gift_cards SET status = 'ACTIVE' WHERE card_id = ?", (owner_id,))
                    self._log_ledger(owner_id, ts, "REVERSE", new_lot_ref, lot_exp, 0.0, amt, amt)

                total_restored = round(total_restored + amt, 2)

            self.conn.execute("UPDATE redemptions SET reversed = 1 WHERE txn_id = ?", (orig_txn_id,))
            self._flow(ym)["reversals"] = round(self._flow(ym)["reversals"] + total_restored, 2)
            status = "APPROVED" if total_restored > 0 else "DECLINED"
            reason = "OK" if total_restored == float(orig["amount_applied"]) else ("PARTIAL_EXPIRED" if total_restored > 0 else "LOT_EXPIRED")
            self._record_decision(
                line_no, "REVERSE", ts, owner_id, rev_id, float(orig["amount_applied"]), total_restored, status, reason
            )

    def handle_chargeback(self, line_no: int, parts: List[str]) -> None:
        _, ts, wallet_id, load_ref = [p.strip() for p in parts]
        dt = parse_ts(ts)
        ym = ts[:7]
        self._expire_due_lots(dt.strftime("%Y-%m-%d"), ts)

        lot = self.conn.execute(
            "SELECT * FROM lots WHERE owner_id = ? AND lot_ref = ?", (wallet_id, load_ref)
        ).fetchone()
        already_charged_back = self.conn.execute(
            "SELECT 1 FROM ledger_entries WHERE entry_type = 'CHARGEBACK' AND lot_ref = ?",
            (load_ref,),
        ).fetchone()
        if not lot or already_charged_back:
            self._record_decision(line_no, "CHARGEBACK", ts, wallet_id, load_ref, 0.0, 0.0, "DECLINED", "INVALID_LOT")
            return

        load_dt = parse_ts(lot["created_ts"])
        days_since = (dt.date() - load_dt.date()).days
        orig_amt = round(float(lot["original_amount"]), 2)

        if days_since > 120:
            self._record_decision(
                line_no, "CHARGEBACK", ts, wallet_id, load_ref, orig_amt, 0.0, "DECLINED", "OUTSIDE_WINDOW"
            )
            return

        rem = round(float(lot["remaining_amount"]), 2)
        clawback_shortfall = round(orig_amt - rem, 2)

        with self.conn:
            if clawback_shortfall > 0:
                self.conn.execute(
                    "UPDATE lots SET remaining_amount = ?, status = 'ACTIVE' WHERE lot_ref = ?",
                    (-clawback_shortfall, load_ref),
                )
                self.conn.execute("UPDATE wallets SET is_blocked = 1 WHERE wallet_id = ?", (wallet_id,))
            else:
                self.conn.execute(
                    "UPDATE lots SET remaining_amount = 0.0, status = 'CHARGED_BACK' WHERE lot_ref = ?",
                    (load_ref,),
                )

            self._flow(ym)["chargebacks"] = round(self._flow(ym)["chargebacks"] + orig_amt, 2)
            self._log_ledger(
                wallet_id, ts, "CHARGEBACK", load_ref, lot["expiry_date"], orig_amt, 0.0, -clawback_shortfall
            )
            self._record_decision(line_no, "CHARGEBACK", ts, wallet_id, load_ref, orig_amt, orig_amt, "APPROVED", "OK")

    def handle_reactivate(self, line_no: int, parts: List[str]) -> None:
        _, ts, owner_id, load_ref = [p.strip() for p in parts]
        dt = parse_ts(ts)
        date_str = dt.strftime("%Y-%m-%d")
        ym = ts[:7]
        self._expire_due_lots(date_str, ts)

        lot = self.conn.execute(
            "SELECT * FROM lots WHERE owner_id = ? AND lot_ref = ?", (owner_id, load_ref)
        ).fetchone()
        if not lot or lot["status"] != "EXPIRED":
            self._record_decision(line_no, "REACTIVATE", ts, owner_id, load_ref, 0.0, 0.0, "DECLINED", "NOT_EXPIRED")
            return

        rem = round(float(lot["remaining_amount"]), 2)
        if int(lot["reactivated_count"]) >= 1:
            self._record_decision(line_no, "REACTIVATE", ts, owner_id, load_ref, rem, 0.0, "DECLINED", "ALREADY_REACTIVATED")
            return

        exp_dt = datetime.strptime(lot["expiry_date"], "%Y-%m-%d")
        days_expired = (dt.date() - exp_dt.date()).days
        if days_expired > 90:
            self._record_decision(line_no, "REACTIVATE", ts, owner_id, load_ref, rem, 0.0, "DECLINED", "GRACE_EXPIRED")
            return

        fee = round(rem * 0.10, 2)
        new_bal = round(rem - fee, 2)
        new_expiry = add_months(dt, 6).strftime("%Y-%m-%d")

        with self.conn:
            self.conn.execute(
                """UPDATE lots
                   SET status = 'ACTIVE', remaining_amount = ?, expiry_date = ?, reactivated_count = reactivated_count + 1
                   WHERE lot_ref = ?""",
                (new_bal, new_expiry, load_ref),
            )
            if lot["owner_type"] == "CARD":
                self.conn.execute(
                    "UPDATE gift_cards SET status = 'ACTIVE', expiry_date = ? WHERE card_id = ?",
                    (new_expiry, owner_id),
                )
            self._flow(ym)["loads"] = round(self._flow(ym)["loads"] + new_bal, 2)
            self._log_ledger(owner_id, ts, "REACTIVATE", load_ref, new_expiry, fee, new_bal, new_bal)
            self._record_decision(line_no, "REACTIVATE", ts, owner_id, load_ref, rem, new_bal, "APPROVED", "OK")

    def handle_report_lost(self, line_no: int, parts: List[str]) -> None:
        _, ts, card_id = [p.strip() for p in parts]
        card = self.conn.execute("SELECT * FROM gift_cards WHERE card_id = ?", (card_id,)).fetchone()
        if not card or card["status"] not in ("ACTIVE", "INACTIVE"):
            return
        with self.conn:
            self.conn.execute(
                "UPDATE gift_cards SET status = 'FROZEN', frozen_ts = ? WHERE card_id = ?",
                (ts, card_id),
            )

    def handle_transfer_lost(self, line_no: int, parts: List[str]) -> None:
        _, ts, old_id, new_id, new_pin = [p.strip() for p in parts]
        dt = parse_ts(ts)
        ym = ts[:7]
        self._expire_due_lots(dt.strftime("%Y-%m-%d"), ts)

        old_card = self.conn.execute("SELECT * FROM gift_cards WHERE card_id = ?", (old_id,)).fetchone()
        if not old_card or old_card["status"] != "FROZEN" or not old_card["frozen_ts"]:
            self._record_decision(line_no, "TRANSFER_LOST", ts, old_id, new_id, 0.0, 0.0, "DECLINED", "NOT_FROZEN")
            return

        if self.conn.execute("SELECT 1 FROM gift_cards WHERE card_id = ?", (new_id,)).fetchone():
            self._record_decision(line_no, "TRANSFER_LOST", ts, old_id, new_id, 0.0, 0.0, "DECLINED", "DUPLICATE_CARD")
            return

        old_lot = self.conn.execute("SELECT * FROM lots WHERE lot_ref = ?", (old_id,)).fetchone()
        bal = round(float(old_lot["remaining_amount"]), 2)

        frozen_dt = parse_ts(old_card["frozen_ts"])
        if (dt.date() - frozen_dt.date()).days < 7:
            self._record_decision(line_no, "TRANSFER_LOST", ts, old_id, new_id, bal, 0.0, "DECLINED", "TOO_EARLY")
            return

        fee = min(50.0, bal)
        new_bal = round(bal - fee, 2)
        expiry_str = old_card["expiry_date"] or (dt + timedelta(days=365)).strftime("%Y-%m-%d")

        with self.conn:
            self.conn.execute("UPDATE gift_cards SET status = 'TRANSFERRED' WHERE card_id = ?", (old_id,))
            self.conn.execute(
                "UPDATE lots SET remaining_amount = 0.0, status = 'TRANSFERRED' WHERE lot_ref = ?",
                (old_id,),
            )
            self.conn.execute(
                """INSERT INTO gift_cards
                   (card_id, issue_ts, card_type, initial_value, channel, pin, buyer_tender, status, activated_ts, expiry_date)
                   VALUES (?, ?, ?, ?, ?, ?, 'TRANSFER', 'ACTIVE', ?, ?)""",
                (new_id, ts, old_card["card_type"], new_bal, old_card["channel"], new_pin, ts, expiry_str),
            )
            self.conn.execute(
                """INSERT INTO lots
                   (lot_ref, owner_type, owner_id, lot_type, source, created_ts, expiry_date, original_amount, remaining_amount, status)
                   VALUES (?, 'CARD', ?, 'CARD', ?, ?, ?, ?, ?, 'ACTIVE')""",
                (new_id, new_id, old_id, ts, expiry_str, new_bal, new_bal),
            )
            self._log_ledger(old_id, ts, "TRANSFER_OUT", old_id, expiry_str, bal, 0.0, 0.0)
            self._log_ledger(new_id, ts, "TRANSFER_IN", new_id, expiry_str, 0.0, new_bal, new_bal)
            self._flow(ym)["redemptions"] = round(self._flow(ym)["redemptions"] + fee, 2)
            self._record_decision(line_no, "TRANSFER_LOST", ts, old_id, new_id, bal, new_bal, "APPROVED", "OK")

    def handle_month_end(self, line_no: int, parts: List[str]) -> None:
        _, ym = [p.strip() for p in parts]
        last_day = month_last_day(ym)
        end_ts = f"{last_day} 23:59:59"
        end_dt = datetime.strptime(last_day, "%Y-%m-%d")

        rows = self.conn.execute(
            """SELECT lot_ref, owner_id, expiry_date, remaining_amount
               FROM lots
               WHERE status = 'ACTIVE' AND expiry_date <= ?""",
            (last_day,),
        ).fetchall()
        with self.conn:
            for r in rows:
                rem = round(float(r["remaining_amount"]), 2)
                self.conn.execute(
                    "UPDATE lots SET status = 'EXPIRED', expired_date = ? WHERE lot_ref = ?",
                    (r["expiry_date"], r["lot_ref"]),
                )
                if rem > 0:
                    self._flow(ym)["expiries"] = round(self._flow(ym)["expiries"] + rem, 2)
                    self._log_ledger(r["owner_id"], end_ts, "EXPIRE", r["lot_ref"], r["expiry_date"], rem, 0.0, 0.0)

            exp_rows = self.conn.execute(
                "SELECT lot_ref, owner_id, expiry_date, remaining_amount FROM lots WHERE status = 'EXPIRED'"
            ).fetchall()
            for er in exp_rows:
                exp_dt = datetime.strptime(er["expiry_date"], "%Y-%m-%d")
                if (end_dt.date() - exp_dt.date()).days > 90:
                    amt = round(float(er["remaining_amount"]), 2)
                    self.conn.execute(
                        "UPDATE lots SET status = 'BREAKAGE', remaining_amount = 0.0 WHERE lot_ref = ?",
                        (er["lot_ref"],),
                    )
                    if amt > 0:
                        self.conn.execute(
                            """INSERT INTO breakage_records (recognition_date, owner_id, lot_ref, expiry_date, amount)
                               VALUES (?, ?, ?, ?, ?)""",
                            (last_day, er["owner_id"], er["lot_ref"], er["expiry_date"], amt),
                        )

            opening = round(self.last_closing_liability, 2)
            fl = self._flow(ym)
            closing = self._all_active_liability()
            expected = round(opening + fl["loads"] - fl["redemptions"] + fl["reversals"] - fl["expiries"] - fl["chargebacks"], 2)
            reconciles = "true" if abs(closing - expected) < 0.02 else "false"
            self.last_closing_liability = closing

            self.conn.execute(
                """INSERT OR REPLACE INTO monthly_reports
                   (month, opening_liability, loads, redemptions, reversals, expiries, chargebacks, closing_liability, reconciles)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ym,
                    opening,
                    round(fl["loads"], 2),
                    round(fl["redemptions"], 2),
                    round(fl["reversals"], 2),
                    round(fl["expiries"], 2),
                    round(fl["chargebacks"], 2),
                    closing,
                    reconciles,
                ),
            )

    def handle_print_statement(self, line_no: int, parts: List[str]) -> None:
        _, target_id = [p.strip() for p in parts]
        rows = self.conn.execute(
            """SELECT owner_id, entry_ts, entry_type, lot_ref, lot_expiry, debit, credit, lot_balance_after, total_balance_after
               FROM ledger_entries
               WHERE owner_id = ?
               ORDER BY entry_id ASC""",
            (target_id,),
        ).fetchall()
        for r in rows:
            self.statements.append(
                {
                    "id": r["owner_id"],
                    "entry_ts": r["entry_ts"],
                    "entry_type": r["entry_type"],
                    "lot_ref": r["lot_ref"],
                    "lot_expiry": r["lot_expiry"],
                    "debit": fmt_money(float(r["debit"])),
                    "credit": fmt_money(float(r["credit"])),
                    "lot_balance_after": fmt_money(float(r["lot_balance_after"])),
                    "total_balance_after": fmt_money(float(r["total_balance_after"])),
                }
            )

    def write_csv_outputs(self) -> None:
        with open(self.output_dir / "decisions.csv", mode="w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "line_no",
                    "command",
                    "ts",
                    "id",
                    "txn_or_ref",
                    "amount_requested",
                    "amount_applied",
                    "result",
                    "reason_code",
                ],
            )
            writer.writeheader()
            writer.writerows(self.decisions)

        with open(self.output_dir / "statements.csv", mode="w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "id",
                    "entry_ts",
                    "entry_type",
                    "lot_ref",
                    "lot_expiry",
                    "debit",
                    "credit",
                    "lot_balance_after",
                    "total_balance_after",
                ],
            )
            writer.writeheader()
            writer.writerows(self.statements)

        liab_rows = self.conn.execute("SELECT * FROM monthly_reports ORDER BY month ASC").fetchall()
        with open(self.output_dir / "liability_report.csv", mode="w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "month",
                    "opening_liability",
                    "loads",
                    "redemptions",
                    "reversals",
                    "expiries",
                    "chargebacks",
                    "closing_liability",
                    "reconciles",
                ]
            )
            for r in liab_rows:
                writer.writerow(
                    [
                        r["month"],
                        fmt_money(float(r["opening_liability"])),
                        fmt_money(float(r["loads"])),
                        fmt_money(float(r["redemptions"])),
                        fmt_money(float(r["reversals"])),
                        fmt_money(float(r["expiries"])),
                        fmt_money(float(r["chargebacks"])),
                        fmt_money(float(r["closing_liability"])),
                        r["reconciles"],
                    ]
                )

        brk_rows = self.conn.execute("SELECT * FROM breakage_records ORDER BY id ASC").fetchall()
        with open(self.output_dir / "breakage_report.csv", mode="w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["recognition_date", "id", "lot_ref", "expiry_date", "amount"])
            for r in brk_rows:
                writer.writerow(
                    [
                        r["recognition_date"],
                        r["owner_id"],
                        r["lot_ref"],
                        r["expiry_date"],
                        fmt_money(float(r["amount"])),
                    ]
                )

    def run_commands(self, commands_path: str) -> None:
        with open(commands_path, mode="r", encoding="utf-8-sig") as f:
            for line_no, raw_line in enumerate(f, start=1):
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("|")
                cmd = parts[0].strip()

                if cmd == "ISSUE":
                    self.handle_issue(line_no, parts)
                elif cmd == "ACTIVATE":
                    self.handle_activate(line_no, parts)
                elif cmd == "LOAD_WALLET":
                    self.handle_load_wallet(line_no, parts)
                elif cmd == "REDEEM":
                    self.handle_redeem(line_no, parts)
                elif cmd == "REVERSE":
                    self.handle_reverse(line_no, parts)
                elif cmd == "CHARGEBACK":
                    self.handle_chargeback(line_no, parts)
                elif cmd == "REACTIVATE":
                    self.handle_reactivate(line_no, parts)
                elif cmd == "REPORT_LOST":
                    self.handle_report_lost(line_no, parts)
                elif cmd == "TRANSFER_LOST":
                    self.handle_transfer_lost(line_no, parts)
                elif cmd == "MONTH_END":
                    self.handle_month_end(line_no, parts)
                elif cmd == "PRINT_STATEMENT":
                    self.handle_print_statement(line_no, parts)
                elif cmd in ("PRINT_LIABILITY_REPORT", "PRINT_BREAKAGE_REPORT"):
                    self.write_csv_outputs()

        self.write_csv_outputs()
        self.conn.close()


def run_pipeline(
    db_path: str,
    stores_csv: str,
    wallets_csv: str,
    commands_txt: str,
    output_dir: str,
) -> None:
    create_database(db_path)
    engine = LedgerEngine(db_path, output_dir)
    engine.load_masters(stores_csv, wallets_csv)
    engine.run_commands(commands_txt)
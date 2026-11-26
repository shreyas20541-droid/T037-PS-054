import csv
import sqlite3
from pathlib import Path

import pytest

from src.processor import run_pipeline


WALLETS = [
    ("WAL-001", "MIN_KYC"),
    ("WAL-002", "MIN_KYC"),
    ("WAL-003", "MIN_KYC"),
    ("WAL-004", "MIN_KYC"),
    ("WAL-005", "MIN_KYC"),
    ("WAL-006", "MIN_KYC"),
    ("WAL-031", "FULL_KYC"),
    ("WAL-040", "FULL_KYC"),
]


def run_case(tmp_path: Path, commands: list[str]) -> tuple[sqlite3.Connection, Path]:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    with (input_dir / "stores.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["store_id", "store_name", "city"])
        writer.writerow(["STR-01", "Test Store", "Bengaluru"])
    with (input_dir / "wallets.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["wallet_id", "customer_name", "mobile", "kyc_tier", "opened_date"])
        for index, (wallet_id, kyc_tier) in enumerate(WALLETS, start=1):
            writer.writerow([wallet_id, f"Customer {index}", f"90000000{index:02d}", kyc_tier, "2025-01-01"])
    commands_path = input_dir / "commands.txt"
    commands_path.write_text("\n".join(commands) + "\n", encoding="utf-8")
    db_path = output_dir / "ledger.db"
    run_pipeline(
        str(db_path),
        str(input_dir / "stores.csv"),
        str(input_dir / "wallets.csv"),
        str(commands_path),
        str(output_dir),
    )
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection, output_dir


def decision_rows(output_dir: Path) -> dict[str, dict[str, str]]:
    with (output_dir / "decisions.csv").open(newline="", encoding="utf-8") as file:
        return {row["txn_or_ref"]: row for row in csv.DictReader(file)}


def test_activation_window_issue_validation_and_online_pin(tmp_path: Path) -> None:
    commands = [
        "ISSUE|2025-01-01 10:00:00|GC-BAD|FIXED|1275|ONLINE|1234|CASH",
        "ISSUE|2025-01-01 10:00:00|GC-LOW|FIXED|50|STORE|1234|CASH",
        "ISSUE|2025-01-01 10:00:00|GC-HIGH|FIXED|10050|STORE|1234|CASH",
        "ISSUE|2025-01-01 10:00:00|GC-MIN|FIXED|100|STORE|1234|CASH",
        "ISSUE|2025-01-01 10:00:00|GC-MAX|FIXED|10000|STORE|1234|CASH",
        "ISSUE|2025-01-01 10:00:00|GC-180|FIXED|500|STORE|1234|CASH",
        "ISSUE|2025-01-01 10:00:00|GC-181|FIXED|500|STORE|1234|CASH",
        "ISSUE|2025-01-01 10:00:00|GC-PIN|FIXED|500|ONLINE|1234|CASH",
        "REDEEM|2025-01-02 10:00:00|PIN-INACTIVE|CARD|GC-PIN|ONLINE|ONLINE|100.00|0.00|100.00|-",
        "ACTIVATE|2025-01-02 11:00:00|GC-PIN",
        "REDEEM|2025-01-02 12:00:00|PIN-MISSING|CARD|GC-PIN|ONLINE|ONLINE|100.00|0.00|100.00|-",
        "REDEEM|2025-01-02 13:00:00|PIN-WRONG|CARD|GC-PIN|ONLINE|ONLINE|100.00|0.00|100.00|0000",
        "REDEEM|2025-01-02 14:00:00|PIN-OK|CARD|GC-PIN|ONLINE|ONLINE|100.00|0.00|100.00|1234",
        "ACTIVATE|2025-06-30 10:00:00|GC-180",
        "ACTIVATE|2025-07-01 10:00:00|GC-181",
    ]
    connection, output_dir = run_case(tmp_path, commands)
    cards = {row["card_id"]: row["status"] for row in connection.execute("SELECT card_id, status FROM gift_cards")}
    decisions = decision_rows(output_dir)

    assert "GC-BAD" not in cards
    assert "GC-LOW" not in cards
    assert "GC-HIGH" not in cards
    assert cards["GC-MIN"] == "VOIDED"
    assert cards["GC-MAX"] == "VOIDED"
    assert cards["GC-180"] == "ACTIVE"
    assert cards["GC-181"] == "VOIDED"
    refund = connection.execute(
        "SELECT refund_date, original_tender, amount FROM card_refunds WHERE card_id='GC-MIN'"
    ).fetchone()
    assert tuple(refund) == ("2025-07-01", "CASH", 100.0)
    assert decisions["PIN-INACTIVE"]["reason_code"] == "NOT_ACTIVATED"
    assert decisions["PIN-MISSING"]["reason_code"] == "PIN_REQUIRED"
    assert decisions["PIN-WRONG"]["reason_code"] == "INVALID_PIN"
    assert decisions["PIN-OK"]["result"] == "APPROVED"
    connection.close()


def test_min_kyc_holding_balance_literal_and_monthly_load_cap(tmp_path: Path) -> None:
    commands = [
        "LOAD_WALLET|2026-07-01 09:00:00|WAL-001|LOAD-A|6000.00|UPI|PAID",
        "LOAD_WALLET|2026-07-01 09:01:00|WAL-001|LOAD-B|BALANCE_TO_10000|UPI|PAID",
        "LOAD_WALLET|2026-07-01 09:02:00|WAL-001|LOAD-C|1.00|UPI|PAID",
        "LOAD_WALLET|2026-07-01 10:00:00|WAL-006|LOAD-D|6000.00|UPI|PAID",
        "REDEEM|2026-07-01 11:00:00|SPEND-D|WALLET|WAL-006|STORE|STR-01|5000.00|0.00|5000.00|-",
        "LOAD_WALLET|2026-07-01 12:00:00|WAL-006|LOAD-E|4000.00|UPI|PAID",
        "LOAD_WALLET|2026-07-01 13:00:00|WAL-006|LOAD-F|500.00|UPI|PAID",
    ]
    connection, output_dir = run_case(tmp_path, commands)
    decisions = decision_rows(output_dir)

    assert decisions["LOAD-B"]["amount_applied"] == "4000.00"
    assert decisions["LOAD-C"]["reason_code"] == "HOLDING_LIMIT"
    assert decisions["LOAD-F"]["reason_code"] == "MONTHLY_LOAD_LIMIT"
    assert connection.execute("SELECT SUM(remaining_amount) FROM lots WHERE owner_id='WAL-001'").fetchone()[0] == 10000
    connection.close()


def test_full_kyc_monthly_load_cap(tmp_path: Path) -> None:
    commands = [
        "LOAD_WALLET|2026-07-01 09:00:00|WAL-031|FULL-LOAD-A|60000.00|UPI|PAID",
        "LOAD_WALLET|2026-07-01 09:01:00|WAL-031|FULL-LOAD-B|40000.00|UPI|PAID",
        "LOAD_WALLET|2026-07-01 09:02:00|WAL-031|FULL-LOAD-C|1.00|UPI|PAID",
    ]
    connection, output_dir = run_case(tmp_path, commands)
    decisions = decision_rows(output_dir)

    assert decisions["FULL-LOAD-A"]["result"] == "APPROVED"
    assert decisions["FULL-LOAD-B"]["result"] == "APPROVED"
    assert decisions["FULL-LOAD-C"]["reason_code"] == "MONTHLY_LOAD_LIMIT"
    assert connection.execute(
        "SELECT SUM(remaining_amount) FROM lots WHERE owner_id='WAL-031'"
    ).fetchone()[0] == 100000
    connection.close()


def test_wallet_daily_spend_limits_use_capped_redemption_amount(tmp_path: Path) -> None:
    commands = [
        "LOAD_WALLET|2026-07-01 09:00:00|WAL-001|MIN-DAILY-LOAD|10000.00|UPI|PAID",
        "REDEEM|2026-07-01 10:00:00|MIN-DAILY-10K|WALLET|WAL-001|STORE|STR-01|10000.00|0.00|10000.00|-",
        "REDEEM|2026-07-01 10:01:00|MIN-DAILY-OVER|WALLET|WAL-001|STORE|STR-01|1.00|0.00|1.00|-",
        "LOAD_WALLET|2026-07-01 09:00:00|WAL-031|FULL-DAILY-LOAD|60000.00|UPI|PAID",
        "REDEEM|2026-07-01 11:00:00|FULL-RESTRICTED|WALLET|WAL-031|STORE|STR-01|1000.00|1.00|60000.00|-",
        "REDEEM|2026-07-01 11:01:00|FULL-DAILY-50K|WALLET|WAL-031|STORE|STR-01|49300.00|0.00|49300.00|-",
        "REDEEM|2026-07-01 11:02:00|FULL-DAILY-OVER|WALLET|WAL-031|STORE|STR-01|1.00|0.00|1.00|-",
    ]
    connection, output_dir = run_case(tmp_path, commands)
    decisions = decision_rows(output_dir)

    assert decisions["MIN-DAILY-10K"]["result"] == "APPROVED"
    assert decisions["MIN-DAILY-OVER"]["reason_code"] == "VELOCITY"
    assert decisions["FULL-RESTRICTED"]["amount_applied"] == "700.00"
    assert decisions["FULL-DAILY-50K"]["result"] == "APPROVED"
    assert decisions["FULL-DAILY-OVER"]["reason_code"] == "VELOCITY"
    connection.close()


def test_card_expiry_uses_three_calendar_years(tmp_path: Path) -> None:
    commands = [
        "ISSUE|2024-02-28 09:00:00|GC-3YR|OPEN|500|STORE|1234|CASH",
        "ACTIVATE|2024-02-29 09:00:00|GC-3YR",
    ]
    connection, _ = run_case(tmp_path, commands)

    assert connection.execute(
        "SELECT expiry_date FROM gift_cards WHERE card_id='GC-3YR'"
    ).fetchone()[0] == "2027-02-28"
    connection.close()


def test_month_end_expiry_reminders_cover_next_30_days(tmp_path: Path) -> None:
    commands = [
        "LOAD_WALLET|2025-04-30 09:00:00|WAL-002|REMINDER-IN|100.00|UPI|PAID",
        "LOAD_WALLET|2025-05-01 09:00:00|WAL-002|REMINDER-OUT|100.00|UPI|PAID",
        "MONTH_END|2026-03",
    ]
    _, output_dir = run_case(tmp_path, commands)
    with (output_dir / "reminders.csv").open(newline="", encoding="utf-8") as file:
        reminders = list(csv.DictReader(file))

    assert reminders == [
        {
            "as_of_date": "2026-03-31",
            "id": "WAL-002",
            "lot_ref": "REMINDER-IN",
            "expiry_date": "2026-04-30",
            "remaining_balance": "100.00",
        }
    ]


def test_restricted_bill_cap_and_promo_first_on_expiry_date(tmp_path: Path) -> None:
    commands = [
        "ISSUE|2026-01-01 09:00:00|GC-CAP|OPEN|1000|STORE|1234|CASH",
        "ACTIVATE|2026-01-01 10:00:00|GC-CAP",
        "REDEEM|2026-01-02 09:00:00|CAP-USE|CARD|GC-CAP|STORE|STR-01|1000.00|1.00|1000.00|-",
        "LOAD_WALLET|2026-01-05 09:00:00|WAL-002|PAID-A|500.00|UPI|PAID",
        "LOAD_WALLET|2026-01-05 09:01:00|WAL-002|LOAD-EC4P|300.00|UPI|PROMO",
        "REDEEM|2026-04-05 18:00:00|PROMO-USE|WALLET|WAL-002|STORE|STR-01|450.00|0.00|450.00|-",
    ]
    connection, output_dir = run_case(tmp_path, commands)
    allocations = connection.execute(
        "SELECT lot_ref, amount FROM redemption_allocations WHERE txn_id='PROMO-USE' ORDER BY id"
    ).fetchall()
    decisions = decision_rows(output_dir)

    assert [(row["lot_ref"], row["amount"]) for row in allocations] == [("LOAD-EC4P", 300), ("PAID-A", 150)]
    assert decisions["CAP-USE"]["amount_applied"] == "700.00"
    assert decisions["CAP-USE"]["reason_code"] == "RESTRICTED_CAP_70"
    connection.close()


def test_later_reversal_skips_expired_consumed_lot(tmp_path: Path) -> None:
    commands = [
        "LOAD_WALLET|2025-09-10 09:00:00|WAL-004|REV-LOT|200.00|UPI|PAID",
        "REDEEM|2025-09-11 09:00:00|REV-SAME|WALLET|WAL-004|STORE|STR-01|100.00|0.00|100.00|-",
        "REVERSE|2025-09-11 10:00:00|REV-SAME|REV-SAME-ID",
        "LOAD_WALLET|2025-09-20 09:00:00|WAL-003|LOAD-EC5A|1000.00|UPI|PAID",
        "LOAD_WALLET|2026-01-15 10:00:00|WAL-003|LOAD-EC5B|800.00|UPI|PAID",
        "REDEEM|2026-09-20 09:00:00|RDM-EC5|WALLET|WAL-003|STORE|STR-01|1500.00|0.00|1500.00|-",
        "REVERSE|2026-09-21 09:00:00|RDM-EC5|RVS-EC5",
    ]
    connection, output_dir = run_case(tmp_path, commands)
    lots = connection.execute(
        "SELECT lot_ref, remaining_amount, expiry_date FROM lots WHERE owner_id='WAL-003' ORDER BY lot_ref"
    ).fetchall()
    decisions = decision_rows(output_dir)

    assert decisions["RVS-EC5"]["amount_applied"] == "500.00"
    assert decisions["RVS-EC5"]["reason_code"] == "PARTIAL_EXPIRED"
    assert any(row["lot_ref"] == "RVS-EC5-2" and row["remaining_amount"] == 500 for row in lots)
    assert next(row for row in lots if row["lot_ref"] == "LOAD-EC5A")["remaining_amount"] == 0
    same_day_lot = connection.execute("SELECT remaining_amount FROM lots WHERE lot_ref='REV-LOT'").fetchone()[0]
    assert same_day_lot == 200
    assert connection.execute("SELECT COUNT(*) FROM lots WHERE lot_type='REVERSAL'").fetchone()[0] == 1
    connection.close()


def test_reactivation_day_90_day_91_and_breakage(tmp_path: Path) -> None:
    commands = [
        "LOAD_WALLET|2026-01-01 09:00:00|WAL-004|LOAD-RA90|500.00|UPI|PROMO",
        "LOAD_WALLET|2026-01-01 09:01:00|WAL-005|LOAD-RA91|500.00|UPI|PROMO",
        "LOAD_WALLET|2026-01-01 09:02:00|WAL-006|LOAD-RA-LIMIT|500.00|UPI|PROMO",
        "MONTH_END|2026-04",
        "LOAD_WALLET|2026-06-29 09:00:00|WAL-006|LOAD-TO-LIMIT|9800.00|UPI|PAID",
        "REACTIVATE|2026-06-30 10:00:00|WAL-004|LOAD-RA90",
        "REACTIVATE|2026-06-30 10:01:00|WAL-006|LOAD-RA-LIMIT",
        "REACTIVATE|2026-07-01 10:00:00|WAL-005|LOAD-RA91",
        "MONTH_END|2026-07",
        "PRINT_BREAKAGE_REPORT",
    ]
    connection, output_dir = run_case(tmp_path, commands)
    decisions = decision_rows(output_dir)
    with (output_dir / "breakage_report.csv").open(newline="", encoding="utf-8") as file:
        breakage = list(csv.DictReader(file))

    assert decisions["LOAD-RA90"]["amount_applied"] == "450.00"
    assert decisions["LOAD-RA91"]["reason_code"] == "GRACE_EXPIRED"
    assert decisions["LOAD-RA-LIMIT"]["reason_code"] == "HOLDING_LIMIT"
    expiry = connection.execute("SELECT expiry_date FROM lots WHERE lot_ref='LOAD-RA90'").fetchone()[0]
    assert expiry == "2026-12-30"
    assert breakage == [
        {
            "recognition_date": "2026-08-01",
            "id": "WAL-005",
            "lot_ref": "LOAD-RA91",
            "expiry_date": "2026-04-01",
            "amount": "500.00",
        },
        {
            "recognition_date": "2026-08-01",
            "id": "WAL-006",
            "lot_ref": "LOAD-RA-LIMIT",
            "expiry_date": "2026-04-01",
            "amount": "500.00",
        }
    ]
    connection.close()


def test_chargeback_day_120_debt_block_and_day_121_rejection(tmp_path: Path) -> None:
    commands = [
        "LOAD_WALLET|2026-05-01 09:00:00|WAL-031|LOAD-EC7|5000.00|UPI|PAID",
        "REDEEM|2026-05-01 10:00:00|EC7-SPEND|WALLET|WAL-031|STORE|STR-01|3000.00|0.00|3000.00|-",
        "LOAD_WALLET|2026-05-01 10:30:00|WAL-031|LOAD-OTHER|5000.00|UPI|PAID",
        "MONTH_END|2026-05",
        "CHARGEBACK|2026-08-29 09:00:00|WAL-031|LOAD-EC7",
        "REDEEM|2026-08-29 10:00:00|EC7-BLOCK|WALLET|WAL-031|STORE|STR-01|100.00|0.00|100.00|-",
        "LOAD_WALLET|2026-08-29 11:00:00|WAL-031|LOAD-TOPUP|4000.00|UPI|PAID",
        "REDEEM|2026-08-29 12:00:00|EC7-UNBLOCK|WALLET|WAL-031|STORE|STR-01|1000.00|0.00|1000.00|-",
    ]
    commands.extend([
        "LOAD_WALLET|2026-08-29 13:00:00|WAL-006|LOAD-CB121|500.00|UPI|PAID",
        "MONTH_END|2026-08",
        "CHARGEBACK|2026-12-28 09:00:00|WAL-006|LOAD-CB121",
    ])
    connection, output_dir = run_case(tmp_path, commands)
    decisions = decision_rows(output_dir)

    assert decisions["LOAD-EC7"]["result"] == "APPROVED"
    assert decisions["EC7-BLOCK"]["reason_code"] == "WALLET_BLOCKED"
    assert decisions["LOAD-TOPUP"]["result"] == "APPROVED"
    assert decisions["EC7-UNBLOCK"]["result"] == "APPROVED"
    assert decisions["LOAD-CB121"]["reason_code"] == "OUTSIDE_WINDOW"
    assert connection.execute("SELECT is_blocked FROM wallets WHERE wallet_id='WAL-031'").fetchone()[0] == 0
    report = connection.execute(
        "SELECT closing_liability, reconciles FROM monthly_reports WHERE month='2026-08'"
    ).fetchone()
    assert tuple(report) == (5500.0, "true")
    connection.close()


def test_chargeback_on_expired_promo_does_not_create_wallet_debt(tmp_path: Path) -> None:
    commands = [
        "LOAD_WALLET|2026-01-01 09:00:00|WAL-040|EXPIRED-PROMO|500.00|UPI|PROMO",
        "MONTH_END|2026-04",
        "CHARGEBACK|2026-05-01 09:00:00|WAL-040|EXPIRED-PROMO",
    ]
    connection, output_dir = run_case(tmp_path, commands)
    decision = decision_rows(output_dir)["EXPIRED-PROMO"]

    assert decision["result"] == "APPROVED"
    assert decision["reason_code"] == "BREAKAGE_ADJUSTED"
    assert tuple(connection.execute(
        "SELECT remaining_amount, status FROM lots WHERE lot_ref='EXPIRED-PROMO'"
    ).fetchone()) == (0.0, "CHARGED_BACK")
    assert connection.execute(
        "SELECT is_blocked FROM wallets WHERE wallet_id='WAL-040'"
    ).fetchone()[0] == 0
    connection.close()


def test_chargeback_rejects_non_load_wallet_lots(tmp_path: Path) -> None:
    commands = [
        "LOAD_WALLET|2026-01-01 09:00:00|WAL-040|LOAD-FOR-REVERSAL|500.00|UPI|PAID",
        "REDEEM|2026-01-02 09:00:00|REDEEM-FOR-REVERSAL|WALLET|WAL-040|STORE|STR-01|100.00|0.00|100.00|-",
        "REVERSE|2026-01-03 09:00:00|REDEEM-FOR-REVERSAL|REVERSAL-LOT",
        "CHARGEBACK|2026-01-04 09:00:00|WAL-040|REVERSAL-LOT",
    ]
    connection, output_dir = run_case(tmp_path, commands)

    assert decision_rows(output_dir)["REVERSAL-LOT"]["reason_code"] == "INVALID_LOT"
    assert connection.execute(
        "SELECT remaining_amount FROM lots WHERE lot_ref='REVERSAL-LOT'"
    ).fetchone()[0] == 100
    connection.close()


def test_lost_card_transfer_preserves_all_active_lots(tmp_path: Path) -> None:
    commands = [
        "ISSUE|2026-01-01 09:00:00|GC-MULTI|OPEN|500|STORE|1234|CASH",
        "ACTIVATE|2026-01-01 09:01:00|GC-MULTI",
        "REDEEM|2026-01-02 09:00:00|RDM-MULTI|CARD|GC-MULTI|STORE|STR-01|300.00|0.00|300.00|-",
        "REVERSE|2026-01-03 09:00:00|RDM-MULTI|REV-MULTI",
        "REPORT_LOST|2026-01-04 09:00:00|GC-MULTI",
        "TRANSFER_LOST|2026-01-11 09:00:00|GC-MULTI|GC-MULTI-NEW|9876",
    ]
    connection, output_dir = run_case(tmp_path, commands)
    decisions = decision_rows(output_dir)

    assert decisions["GC-MULTI-NEW"]["amount_applied"] == "450.00"
    assert connection.execute(
        "SELECT SUM(remaining_amount) FROM lots WHERE owner_id='GC-MULTI-NEW' AND status='ACTIVE'"
    ).fetchone()[0] == 450
    assert connection.execute(
        "SELECT COUNT(*) FROM lots WHERE owner_id='GC-MULTI-NEW' AND remaining_amount > 0"
    ).fetchone()[0] == 2
    connection.close()


def test_card_velocity_full_kyc_daily_cap_and_duplicate_txn(tmp_path: Path) -> None:
    commands = [
        "LOAD_WALLET|2026-05-20 09:00:00|WAL-040|LOAD-FULL|60000.00|UPI|PAID",
        "REDEEM|2026-05-21 10:00:00|FULL-50K|WALLET|WAL-040|STORE|STR-01|50000.00|0.00|50000.00|-",
        "REDEEM|2026-05-21 11:00:00|FULL-EXTRA|WALLET|WAL-040|STORE|STR-01|500.00|0.00|500.00|-",
        "ISSUE|2026-06-01 09:00:00|GC-VEL|OPEN|2000|STORE|1234|CASH",
        "ACTIVATE|2026-06-01 09:01:00|GC-VEL",
        *[
            f"REDEEM|2026-06-15 12:00:0{index}|CARD-{index}|CARD|GC-VEL|STORE|STR-01|100.00|0.00|100.00|-"
            for index in range(5)
        ],
        "REDEEM|2026-06-15 23:59:59|CARD-SIX|CARD|GC-VEL|STORE|STR-01|100.00|0.00|100.00|-",
        "REDEEM|2026-06-16 00:00:00|CARD-SEVEN|CARD|GC-VEL|STORE|STR-01|100.00|0.00|100.00|-",
        "REDEEM|2026-06-16 01:00:00|CARD-0|CARD|GC-VEL|STORE|STR-01|100.00|0.00|100.00|-",
    ]
    connection, output_dir = run_case(tmp_path, commands)
    decisions = decision_rows(output_dir)

    assert decisions["CARD-SIX"]["reason_code"] == "VELOCITY"
    assert decisions["CARD-SEVEN"]["result"] == "APPROVED"
    assert decisions["FULL-50K"]["amount_applied"] == "50000.00"
    assert decisions["FULL-EXTRA"]["reason_code"] == "VELOCITY"
    assert decisions["CARD-0"]["reason_code"] == "DUPLICATE_TXN"
    connection.close()


def test_small_balance_waiver_lost_transfer_and_output_headers(tmp_path: Path) -> None:
    commands = [
        "ISSUE|2026-01-01 09:00:00|GC-WAIVE|OPEN|500|STORE|1234|CASH",
        "ACTIVATE|2026-01-01 10:00:00|GC-WAIVE",
        "REDEEM|2026-01-02 09:00:00|WAIVE-1|CARD|GC-WAIVE|STORE|STR-01|495.00|0.00|495.00|-",
        "REDEEM|2026-01-02 10:00:00|WAIVE-2|CARD|GC-WAIVE|STORE|STR-01|20.00|0.00|20.00|-",
        "REPORT_LOST|2026-01-03 09:00:00|GC-LOST",
        "ISSUE|2026-01-03 09:00:00|GC-LOST|OPEN|500|STORE|1234|CASH",
        "ACTIVATE|2026-01-03 10:00:00|GC-LOST",
        "REPORT_LOST|2026-01-05 09:00:00|GC-LOST",
        "REDEEM|2026-01-05 10:00:00|LOST-SWIPE|CARD|GC-LOST|STORE|STR-01|100.00|0.00|100.00|-",
        "TRANSFER_LOST|2026-01-11 09:00:00|GC-LOST|GC-NEW|9876",
        "TRANSFER_LOST|2026-01-12 09:00:00|GC-LOST|GC-NEW|9876",
        "PRINT_STATEMENT|GC-NEW",
    ]
    connection, output_dir = run_case(tmp_path, commands)
    decisions = decision_rows(output_dir)
    output_files = {path.name for path in output_dir.glob("*.csv")}
    with (output_dir / "decisions.csv").open(newline="", encoding="utf-8") as file:
        transfer_decisions = [row for row in csv.DictReader(file) if row["command"] == "TRANSFER_LOST"]
    with (output_dir / "decisions.csv").open(newline="", encoding="utf-8") as file:
        decision_headers = next(csv.reader(file))
    with (output_dir / "statements.csv").open(newline="", encoding="utf-8") as file:
        statement_headers = next(csv.reader(file))
    with (output_dir / "liability_report.csv").open(newline="", encoding="utf-8") as file:
        liability_headers = next(csv.reader(file))
    with (output_dir / "breakage_report.csv").open(newline="", encoding="utf-8") as file:
        breakage_headers = next(csv.reader(file))
    with (output_dir / "reminders.csv").open(newline="", encoding="utf-8") as file:
        reminder_headers = next(csv.reader(file))

    assert decisions["WAIVE-2"]["reason_code"] == "SMALL_BALANCE_WAIVER"
    assert decisions["LOST-SWIPE"]["reason_code"] == "CARD_FROZEN"
    assert transfer_decisions[0]["reason_code"] == "TOO_EARLY"
    assert transfer_decisions[1]["reason_code"] == "OK"
    assert transfer_decisions[1]["amount_applied"] == "450.00"
    assert output_files == {
        "decisions.csv",
        "statements.csv",
        "liability_report.csv",
        "breakage_report.csv",
        "reminders.csv",
    }
    assert decision_headers == [
        "line_no", "command", "ts", "id", "txn_or_ref", "amount_requested", "amount_applied", "result", "reason_code"
    ]
    assert statement_headers == [
        "id", "entry_ts", "entry_type", "lot_ref", "lot_expiry", "debit", "credit", "lot_balance_after", "total_balance_after"
    ]
    assert liability_headers == [
        "month", "opening_liability", "loads", "redemptions", "reversals", "expiries", "chargebacks", "closing_liability", "reconciles"
    ]
    assert breakage_headers == ["recognition_date", "id", "lot_ref", "expiry_date", "amount"]
    assert reminder_headers == ["as_of_date", "id", "lot_ref", "expiry_date", "remaining_balance"]
    connection.close()
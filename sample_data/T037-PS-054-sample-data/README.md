# PS-054 – Gift Card and Wallet Ledger Engine with Expiry and Breakage — Sample Data Pack

Prepared by 10 SECONDS for Team T037.

Domain: Commerce — Stored Value. Build the stored-value ledger a retail chain uses to issue and redeem gift cards and wallet balances across stores and online, enforce RBI PPI-style limits and KYC tiers, expire balances with grace periods, recognise breakage, and handle partial redemptions, reversals and chargebacks. Finance uses it to report liability and breakage every month. Command-file driven; no UI.

## Files

| File | What it holds | Rows |
|---|---|---|
| `stores.csv` | Physical stores (ONLINE is a virtual channel, not a store) | 10 |
| `wallets.csv` | Wallet holders with KYC tier | 60 |
| `commands.txt` | Command file; process strictly in file order. | 1222 |

Row counts exclude the header line (CSV) and comment lines (commands.txt).

## Column reference

Conventions: dates `YYYY-MM-DD`; timestamps `YYYY-MM-DD HH:MM:SS` (24h, IST, no timezone suffix); money in Rs as plain decimals with at most 2 decimals (no commas, no symbol); percentages as plain numbers (12.5 = 12.5%); booleans `true`/`false`; IDs uppercase with a fixed prefix.

### `stores.csv`

| Column | Type | Units / format | Allowed values | Notes |
|---|---|---|---|---|
| `store_id` | string | STR-nn |  |  |
| `store_name` | string |  |  |  |
| `city` | string |  |  |  |

### `wallets.csv`

| Column | Type | Units / format | Allowed values | Notes |
|---|---|---|---|---|
| `wallet_id` | string | WAL-nnn |  | Primary key |
| `customer_name` | string |  |  |  |
| `mobile` | string | 10 digits |  |  |
| `kyc_tier` | enum |  | MIN_KYC/FULL_KYC | Holding and monthly-load limits |
| `opened_date` | date |  |  |  |

### General notes

- Where the brief is silent: a gift card's single lot is identified by its card_id; the day count for 'day N' uses calendar days from the event timestamp's date (issue date + N days); amounts to the paisa.

## Command file format

`commands.txt` holds one command per line in the form `COMMAND_NAME|arg1|arg2|...`. Commands MUST be processed strictly in file order. Lines starting with `#` are comments and blank lines are ignored. Every command used in this pack, with its argument order:

| Command | Arguments (in order) | Meaning |
|---|---|---|
| `ISSUE` | `ts|card_id|FIXED or OPEN|value|channel|pin|buyer_tender` | Issue a gift card (inactive until ACTIVATE) |
| `ACTIVATE` | `ts|card_id` | Activate; must be within 180 days of issue |
| `LOAD_WALLET` | `ts|wallet_id|load_ref|amount|source|PAID or PROMO` | Load a lot; load_ref is the lot id; amount may be the literal BALANCE_TO_10000 (see planted scenarios) |
| `REDEEM` | `ts|txn_id|CARD or WALLET|id|STORE or ONLINE|store_id or ONLINE|bill_total|restricted_amount|amount_requested|pin or -` | Redeem; restricted_amount is the gold/electronics portion of the bill |
| `REVERSE` | `ts|original_txn_id|reversal_id` | Reverse a redemption |
| `CHARGEBACK` | `ts|wallet_id|load_ref` | Chargeback on a load lot |
| `REACTIVATE` | `ts|wallet_or_card_id|load_ref` | Reactivate an expired lot (card lots use the card_id as load_ref) |
| `REPORT_LOST` | `ts|card_id` | Freeze a card |
| `TRANSFER_LOST` | `ts|old_card_id|new_card_id|new_pin` | Move balance to a new card after 7 quiet days, Rs 50 fee |
| `MONTH_END` | `YYYY-MM` | Run reminders, expiries, breakage recognition and the liability report for the month |
| `PRINT_STATEMENT` | `id` | Append the card/wallet statement |
| `PRINT_LIABILITY_REPORT` | `` | Write liability_report.csv (one row per month) |
| `PRINT_BREAKAGE_REPORT` | `` | Write breakage_report.csv |

## Planted scenarios

Every edge case from the brief is planted at the location given. Line numbers count the header as line 1 for CSVs and count every physical line (including comments) for commands.txt.

- **EC1: Card activation on day 180 after issuance** — commands.txt line 1200: ACTIVATE GC-0171 exactly 180 days after its ISSUE timestamp (allowed, last valid day); line 1077: ACTIVATE GC-0172 on day 181 → card already auto-voided and refunded to the buyer's original tender, activation rejected
- **EC2: Load that brings a MIN_KYC wallet to exactly Rs 10,000** — commands.txt line 661: WAL-001 (MIN_KYC) loads 6000.00; line 662: amount literal BALANCE_TO_10000 means 'load exactly the amount that brings the held balance to 10,000.00' (compute it from your ledger — it must be accepted); line 663: a further load of 1.00 must be rejected in full (holding limit)
- **EC3: Bill of Rs 1,000 with Rs 1 of electronics (70% cap applies)** — commands.txt line 776: REDEEM GC-0001 against bill_total 1000.00 with restricted_amount 1.00 (electronics) and amount_requested 1000.00 → cap 70% = 700.00 from the card, 300.00 must come from other tender (partial approval or decline per your documented choice)
- **EC4: Redemption consuming a promotional lot expiring today** — commands.txt line 487: promo lot LOAD-EC4P of 300.00 on WAL-002 loaded 2026-01-05 (expires 2026-04-05); line 759: redemption of 450.00 on 2026-04-05 18:00:00 must consume the promo lot first (still valid on its expiry date) then paid lots
- **EC5: Reversal of a redemption that consumed two lots, done the next day after one lot expired** — commands.txt line 97/line 516: WAL-003 lots LOAD-EC5A (1000.00, expires 2026-09-20) and LOAD-EC5B (800.00); line 1158: redemption 1500.00 on 2026-09-20 consumes 1000 + 500; line 1162: REVERSE on 2026-09-21 — the 1000.00 part cannot be restored beyond its (now past) expiry, the 500.00 part returns as a new lot with LOAD-EC5B's original expiry
- **EC6: Reactivation request on day 90 after expiry** — commands.txt line 474/line 983: WAL-004 promo lot expired 2026-04-01, REACTIVATE on 2026-06-30 = day 90 (allowed: 10% fee, new 6-month expiry); line 475/line 986: WAL-005 identical lot, REACTIVATE on 2026-07-01 = day 91 → rejected, value becomes breakage on 2026-08-01
- **EC7: Chargeback on day 120 for a lot that is 60% consumed** — commands.txt line 832: WAL-031 load LOAD-EC7 5000.00 on 2026-05-01; line 859: 3000.00 consumed (60%); line 1124: CHARGEBACK on 2026-08-29 = day 120 → claw back the lot, wallet goes negative by 3000.00 and is BLOCKED for loads until positive
- **EC8: Sixth redemption at 23:59:59 and seventh at 00:00:00** — commands.txt line 943–line 949: GC-0002 redeems 100.00 five times on 2026-06-15, the sixth at 23:59:59 (line 949) → DECLINED VELOCITY; the seventh at 2026-06-16 00:00:00 (line 950) is the first of a new day → approved
- **Extra: chargeback on day 121** — commands.txt line 919: CHARGEBACK on LOAD-CB2 121 days after the load → outside the 120-day window, reject; the other 7 chargebacks (line 796, line 1027, line 1132, line 1079, line 1185, line 1196 and EC7) are in window
- **Extra: second reactivation of the same lot** — commands.txt line 1024: LOAD-RA0 was already reactivated once (line 503) → reject (one reactivation per lot)
- **Extra: lost-card transfers** — REPORT_LOST / TRANSFER_LOST pairs at line 958/line 973, line 626/line 646, line 579/line 602, line 716/line 729, line 261/line 281: the fourth pair has a swipe attempt on the frozen card at line 720 (must be declined CARD_FROZEN; transfer still allowed since no transaction succeeded — document) and the fifth transfer is requested after only 6 days → reject TOO_EARLY
- **Extra: invalid issuance values** — commands.txt line 614: value 1275 (not a multiple of 50) → reject; line 615: 10050 (> 10,000) → reject; line 616: 10000 exactly → accept
- **Extra: PIN and activation checks** — commands.txt line 665: ONLINE redemption without PIN → DECLINED PIN_REQUIRED; line 666: wrong PIN → DECLINED; line 669: GC-0180 never activated → DECLINED NOT_ACTIVATED
- **Extra: FULL_KYC daily spend limit** — commands.txt line 875+line 876: WAL-040 spends exactly 50,000.00 on 2026-05-21; line 882: a further 500.00 the same day → DECLINED VELOCITY
- **Extra: MIN_KYC monthly load limit** — commands.txt line 990, line 1010: WAL-006 loads 6000 + 4000 = 10,000.00 in July 2026 (allowed; the 5000 redemption at line 992 keeps holding under 10,000); line 1028: 500.00 more in July → rejected in full (monthly load cap)
- **Extra: small-balance waiver** — commands.txt line 575: GC-0004 (500.00) redeems 495.00 leaving 5.00; line 600: redemption of 20.00 → card pays 5.00 and is closed to zero with the shortfall waived once (card balance < 10)
- **Extra: duplicate txn_id** — commands.txt line 195: REDEEM reuses txn id RDM-00360 already processed → reject DUPLICATE_TXN
- **Extra: reversals** — 30 REVERSE commands in total: 20 same-day (e.g. line 401), 9 later-day (e.g. line 470) plus EC5; a same-day reversal restores the exact lots, a later one creates a new lot with the original expiry
- **Extra: month ends** — 15 MONTH_END commands (first at line 49, last at line 1201) run reminders, expiries, breakage and the liability reconciliation; the lot in EC6 becomes breakage at MONTH_END|2026-07

## What is NOT in this pack

Expected outputs are not provided for this set. Validate your engine with your own test suite against the rules in your brief (BR-01 … BR-nn). A separate hidden input set with the same schema will be used at final evaluation; outputs are compared field-for-field, so follow the column formats above exactly in your own output.

## Output format expected at evaluation

Produce these files (CSV, header row, same conventions as above, rows in the order stated). Numeric columns must be plain decimals with exactly the decimals stated.

### `decisions.csv`

One row per REDEEM/REVERSE/LOAD/CHARGEBACK/REACTIVATE/TRANSFER command in file order.

Columns in order: `line_no`, `command`, `ts`, `id`, `txn_or_ref`, `amount_requested`, `amount_applied`, `result`, `reason_code`

### `statements.csv`

Lot-level statement rows per PRINT_STATEMENT.

Columns in order: `id`, `entry_ts`, `entry_type`, `lot_ref`, `lot_expiry`, `debit`, `credit`, `lot_balance_after`, `total_balance_after`

### `liability_report.csv`

One row per month-end.

Columns in order: `month`, `opening_liability`, `loads`, `redemptions`, `reversals`, `expiries`, `chargebacks`, `closing_liability`, `reconciles`

### `breakage_report.csv`

One row per lot recognised as breakage.

Columns in order: `recognition_date`, `id`, `lot_ref`, `expiry_date`, `amount`

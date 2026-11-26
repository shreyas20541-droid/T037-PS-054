# T037-PS-054: Gift Card and Wallet Ledger Engine with Expiry and Breakage

A deterministic, command-file-driven stored-value ledger engine built in **Python** and **SQLite** for **Team T037** under **Problem Statement PS-054** (*10 SECONDS Capstone*). The engine processes gift card lifecycle operations, manages wallet loads across KYC tiers, allocates redemptions across expiring lots using FEFO/priority rules, handles same-day and later-day reversals, processes chargebacks and grace-period reactivations, transfers lost-card balances, and produces monthly liability reconciliation, reminder, and breakage reports. It has no UI or runtime third-party dependencies.

## Demo Video

- **5-Minute Capstone Walkthrough:** [Watch the T037-PS-054 Demo Video](<your-demo-video-link>)

## Architecture & Data Model

`src/main.py` parses the input and output directory options (`--input-dir` and `--output-dir`) and invokes the processing pipeline. `src/processor.py` ingests the master CSVs, processes each nonblank, noncomment command strictly in physical file order without sorting, applies the business rules (`BR-01` through `BR-16`), writes double-entry ledger entries and decision records, and generates the CSV and text reports. `src/database.py` creates the SQLite schema and database connections. `tests/test_ledger.py` exercises the major business rules and boundary edge cases using temporary databases and input files.

Each monetary balance is tracked by discrete tranche (`lot`) so expiry and redemption allocation remain auditable. SQLite (`ledger.db`) stores the wallet and card entities, polymorphic lots (`CARD`, `PAID`, `PROMO`, `REVERSAL`), redemptions and their exact source allocations (`redemption_allocations`), double-entry ledger entries (`ledger_entries`), monthly reports (`monthly_reports`), breakage records (`breakage_records`), and original-tender refunds (`card_refunds`) for auto-voided cards. Runtime state and output files are written under `data/output/` by default.

## Repository Structure

```text
T037-PS-054/
├── src/
│   ├── __init__.py
│   ├── main.py                                    # CLI entry point (--input-dir / --output-dir)
│   ├── processor.py                               # Sequential pipeline, command dispatcher, and rule engine
│   └── database.py                                # SQLite connection setup and relational schema DDL
├── tests/
│   └── test_ledger.py                             # Automated pytest suite (16 tests covering rules & edge cases)
├── docs/
│   ├── System Design Document - T037-PS-054.pdf   # Final 18-section System Design Document (PDF)
│   ├── DESIGN_DOCUMENT.md                         # Architectural overview, ER schema, lifecycle, and rule register
│   └── DECISION_LOG.md                            # Recorded boundary decisions and sample-pack notes
├── data/
│   ├── input/                                     # Default/custom input directory (commands.txt)
│   └── output/                                    # Runtime SQLite database (ledger.db) and generated reports
│       ├── decisions.csv
│       ├── statements.csv
│       ├── liability_report.csv
│       ├── breakage_report.csv
│       ├── reminders.csv
│       └── report.txt
├── sample_data/
│   └── T037-PS-054-sample-data/
│       ├── README.md                              # Sample dataset documentation and scenario guide
│       ├── stores.csv                             # Physical store master data
│       ├── wallets.csv                            # Customer wallet master data (MIN_KYC / FULL_KYC)
│       └── commands.txt                           # Full pipe-delimited benchmark command stream
├── .gitignore
└── README.md
```

## Requirements and Commands

- **Python 3.10 or later** (uses standard-library `sqlite3`, `csv`, and `datetime`)
- **`pytest`** to run the automated test suite
- No additional third-party package is required to run the engine

### 1. Environment Setup (PowerShell)

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install pytest
```

### 2. Run the Ledger Engine

From the repository root (`T037-PS-054`):

```powershell
python -m src.main
```

The engine defaults to `sample_data/T037-PS-054-sample-data` for input and `data/output` for output. Directories can be changed with `--input-dir` and `--output-dir`:

```powershell
python -m src.main --input-dir path\to\input --output-dir path\to\output
```

The input directory must contain `stores.csv`, `wallets.csv`, and `commands.txt`. Commands are pipe-delimited; lines beginning with `#` and blank lines are skipped, and all other commands are processed in order without sorting.

### 3. Run the Automated Test Suite

```powershell
python -m pytest -v
```

Expected result: **16 passed**.

## Input Schemas

All dates use `YYYY-MM-DD`; timestamps use `YYYY-MM-DD HH:MM:SS` in IST without a timezone suffix. IDs are strings, and money is in Rs with up to two decimal places.

| File | Columns | Description |
| :--- | :--- | :--- |
| `stores.csv` | `store_id, store_name, city` | Physical stores; `ONLINE` is a virtual channel, not a store. |
| `wallets.csv` | `wallet_id, customer_name, mobile, kyc_tier, opened_date` | Wallet holders; `kyc_tier` is `MIN_KYC` or `FULL_KYC`. |
| `commands.txt` | `COMMAND_NAME\|arg1\|arg2\|...` | Ordered `ISSUE`, `ACTIVATE`, `LOAD_WALLET`, `REDEEM`, `REVERSE`, `CHARGEBACK`, `REACTIVATE`, `REPORT_LOST`, `TRANSFER_LOST`, `MONTH_END`, and `PRINT` commands. |

The command argument order and the full source data notes are detailed in [the sample-data README](sample_data/T037-PS-054-sample-data/README.md).

## Implemented Business Rules (BR-01 to BR-16)

| Rule ID | Feature Area | Summary of Behavior |
| :--- | :--- | :--- |
| **BR-01** | Gift Card Issuance | Validates denominations (Rs 100–Rs 10,000 in multiples of Rs 50) and rejects duplicate IDs; creates `INACTIVE` card + backing `CARD` lot. |
| **BR-02** | Activation Window | Activates within 180 calendar days with a 365-day expiry; attempts after day 180 mark the card `VOIDED`, close the lot at zero, and record original-tender refunds in `card_refunds`. |
| **BR-03** | Wallet Load Limits | Enforces `MIN_KYC` (Rs 10,000 holding & monthly gross-load caps), `FULL_KYC` (Rs 200,000 holding cap), `BALANCE_TO_10000` top-ups, and unique load refs. |
| **BR-04** | Load Lot Expiry | Assigns 90-day expiry to `PROMO` loads and 365-day expiry to `PAID` loads; lots remain redeemable on their expiry date. |
| **BR-05** | Redemption Validation | Rejects duplicate `txn_id`, inactive/frozen cards, blocked wallets, and `ONLINE` card redemptions lacking a valid PIN. |
| **BR-06** | Restricted-Goods Cap | Caps stored-value contribution at 70% of `bill_total` when `restricted_amount > 0` (`PARTIAL` / `RESTRICTED_CAP_70`). |
| **BR-07** | FEFO Allocation | Consumes `PROMO` lots first, followed by earliest `expiry_date` and earliest `created_ts` (First-Expiring-First-Out). |
| **BR-08** | Insufficient Funds & Waiver | Declines with `INSUFFICIENT_FUNDS`, or applies a one-time small-balance waiver when an active card has `< Rs 10` remaining and closes the card. |
| **BR-09** | Velocity & Daily Spend Caps | Enforces max 5 approved redemptions/day per card and Rs 50,000 daily applied spend cap for `FULL_KYC` wallets (`VELOCITY`). |
| **BR-10** | Redemption Reversals | Same-day `REVERSE` restores original lots; later-day `REVERSE` issues `REVERSAL` lots with original expiry while skipping expired tranches. |
| **BR-11** | Load Chargebacks | Claws back full original load within 120 days; creates negative lot debt and blocks redemptions until future top-ups offset the balance. |
| **BR-12** | Grace-Period Reactivation | Reactivates an `EXPIRED` lot once within 90 days of expiry with a 10% fee deduction and a 6-month new expiry (`GRACE_EXPIRED` on day 91+). |
| **BR-13** | Lost-Card Freeze | `REPORT_LOST` sets card status to `FROZEN`, blocking subsequent redemptions with `CARD_FROZEN`. |
| **BR-14** | Lost-Card Transfer | `TRANSFER_LOST` after a 7-day quiet period transfers balance minus `min(Rs 50, balance)` to a new card ID while preserving lot expiry. |
| **BR-15** | Month-End Expiry & Breakage | `MONTH_END` expires lots on/before month end, recognizes breakage on lots `> 90` days past expiry, and generates 30-day upcoming expiry reminders. |
| **BR-16** | Ledger & Reconciliation | Emits all required CSV reports and verifies `opening + loads - redemptions + reversals - expiries - chargebacks = closing` within Rs 0.02. |

## Generated Output Reports (`data/output/`)

The reports are written to the output directory. Money columns are formatted with exactly two decimal places. Decision rows are emitted for `LOAD_WALLET`, `REDEEM`, `REVERSE`, `CHARGEBACK`, `REACTIVATE`, and `TRANSFER_LOST`; issuance and activation outcomes are reflected in database/card state rather than `decisions.csv`.

| File | Columns, in order | Contents |
| :--- | :--- | :--- |
| `decisions.csv` | `line_no, command, ts, id, txn_or_ref, amount_requested, amount_applied, result, reason_code` | Per-command decisions, preserving source line numbers and order. |
| `statements.csv` | `id, entry_ts, entry_type, lot_ref, lot_expiry, debit, credit, lot_balance_after, total_balance_after` | Lot-level ledger entries for each `PRINT_STATEMENT` command. |
| `liability_report.csv` | `month, opening_liability, loads, redemptions, reversals, expiries, chargebacks, closing_liability, reconciles` | One row for each processed month end, including the reconciliation flag. |
| `breakage_report.csv` | `recognition_date, id, lot_ref, expiry_date, amount` | Positive expired lot balances recognized as breakage. |
| `reminders.csv` | `as_of_date, id, lot_ref, expiry_date, remaining_balance` | Lots expiring in the 30 days after each processed month end. |
| `report.txt` | — | Human-readable execution summary report. |

Unactivated cards are automatically voided once day 180 has passed (activation on day 180 remains valid). The original tender and amount of each refund are persisted in the `card_refunds` table in `ledger.db`.

## Documentation & Submission Artifacts

- [System Design Document (PDF)](docs/System%20Design%20Document%20-%20T037-PS-054.pdf)
- [Design Document (Markdown)](docs/DESIGN_DOCUMENT.md)
- [Decision Log](docs/DECISION_LOG.md)
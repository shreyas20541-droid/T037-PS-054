# T037 PS-054: Gift Card and Wallet Ledger Engine

A command-file-driven stored-value ledger for issuing and redeeming gift cards, managing wallet loads, allocating redemptions across expiring lots, processing reversals and chargebacks, transferring lost-card balances, and producing monthly liability and breakage reports. The engine is implemented in Python with SQLite; it has no UI or runtime third-party dependencies.

## Architecture

`src/main.py` parses the input and output directory options and invokes the pipeline. `src/processor.py` ingests the master CSVs, processes each nonblank, noncomment command in physical file order, applies the business rules, writes ledger entries and decisions, and generates the CSV reports. `src/database.py` creates the SQLite schema and connections. `tests/test_ledger.py` exercises the major business rules using temporary databases and input files.

Each monetary balance is tracked by lot so expiry and redemption allocation remain auditable. SQLite stores the wallet and card entities, lots, redemptions and their allocations, ledger entries, monthly reports, breakage records, and original-tender refunds for auto-voided cards. Runtime state and output files are written under `data/output/` by default.

## Directory Structure

```text
T037-PS-054/
|-- data/
|   `-- output/                         # ledger.db and generated CSV reports
|-- docs/
|   |-- DECISION_LOG.md
|   `-- DESIGN_DOCUMENT.md
|-- sample_data/
|   `-- T037-PS-054-sample-data/
|       |-- stores.csv
|       |-- wallets.csv
|       `-- commands.txt
|-- src/
|   |-- database.py
|   |-- main.py
|   `-- processor.py
`-- tests/
    `-- test_ledger.py
```

## Requirements and Commands

- Python 3.10 or later
- `pytest` to run the test suite
- No additional package is required to run the engine

From the repository root:

```powershell
python -m src.main
python -m pytest -v
```

The engine defaults to `sample_data/T037-PS-054-sample-data` for input and `data/output` for output. Directories can be changed with `--input-dir` and `--output-dir`:

```powershell
python -m src.main --input-dir path\to\input --output-dir path\to\output
```

The input directory must contain `stores.csv`, `wallets.csv`, and `commands.txt`. Commands are pipe-delimited. Lines beginning with `#` and blank lines are skipped; all other commands are processed in order without sorting.

## Input Schemas

All dates use `YYYY-MM-DD`; timestamps use `YYYY-MM-DD HH:MM:SS` in IST without a timezone suffix. IDs are strings, and money is in Rs with up to two decimal places.

| File | Columns | Description |
|---|---|---|
| `stores.csv` | `store_id, store_name, city` | Physical stores; `ONLINE` is a virtual channel, not a store. |
| `wallets.csv` | `wallet_id, customer_name, mobile, kyc_tier, opened_date` | Wallet holders; `kyc_tier` is `MIN_KYC` or `FULL_KYC`. |
| `commands.txt` | `COMMAND_NAME|arg1|arg2|...` | Ordered ISSUE, ACTIVATE, LOAD_WALLET, REDEEM, REVERSE, CHARGEBACK, REACTIVATE, REPORT_LOST, TRANSFER_LOST, MONTH_END, and PRINT commands. |

The command argument order and the full source data notes are in [the sample-data README](sample_data/T037-PS-054-sample-data/README.md).

## Generated CSVs

The reports are written to the output directory. Money columns are formatted with exactly two decimal places. Decision rows are emitted for `LOAD_WALLET`, `REDEEM`, `REVERSE`, `CHARGEBACK`, `REACTIVATE`, and `TRANSFER_LOST`; issuance and activation outcomes are reflected in database/card state rather than `decisions.csv`.

| File | Columns, in order | Contents |
|---|---|---|
| `decisions.csv` | `line_no, command, ts, id, txn_or_ref, amount_requested, amount_applied, result, reason_code` | Per-command decisions, preserving source line numbers and order. |
| `statements.csv` | `id, entry_ts, entry_type, lot_ref, lot_expiry, debit, credit, lot_balance_after, total_balance_after` | Lot-level ledger entries for each `PRINT_STATEMENT` command. |
| `liability_report.csv` | `month, opening_liability, loads, redemptions, reversals, expiries, chargebacks, closing_liability, reconciles` | One row for each processed month end, including the reconciliation flag. |
| `breakage_report.csv` | `recognition_date, id, lot_ref, expiry_date, amount` | Positive expired lot balances recognized as breakage. |
| `reminders.csv` | `as_of_date, id, lot_ref, expiry_date, remaining_balance` | Lots expiring in the 30 days after each processed month end. |

Unactivated cards are automatically voided once day 180 has passed (activation on day 180 remains valid). The original tender and amount of each refund are persisted in the `card_refunds` table in `ledger.db`.

The schema and rule register are described in [the design document](docs/DESIGN_DOCUMENT.md); implementation tradeoffs are listed in [the decision log](docs/DECISION_LOG.md).
import sqlite3
from pathlib import Path


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def create_database(db_path: str) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path)
    with conn:
        conn.executescript(
            """
            DROP TABLE IF EXISTS redemption_allocations;
            DROP TABLE IF EXISTS redemptions;
            DROP TABLE IF EXISTS ledger_entries;
            DROP TABLE IF EXISTS monthly_reports;
            DROP TABLE IF EXISTS breakage_records;
            DROP TABLE IF EXISTS lots;
            DROP TABLE IF EXISTS gift_cards;
            DROP TABLE IF EXISTS wallets;
            DROP TABLE IF EXISTS stores;

            CREATE TABLE stores (
                store_id TEXT PRIMARY KEY,
                store_name TEXT NOT NULL,
                city TEXT NOT NULL
            );

            CREATE TABLE wallets (
                wallet_id TEXT PRIMARY KEY,
                customer_name TEXT NOT NULL,
                mobile TEXT NOT NULL,
                kyc_tier TEXT NOT NULL CHECK (kyc_tier IN ('MIN_KYC', 'FULL_KYC')),
                opened_date TEXT NOT NULL,
                is_blocked INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE gift_cards (
                card_id TEXT PRIMARY KEY,
                issue_ts TEXT NOT NULL,
                card_type TEXT NOT NULL CHECK (card_type IN ('FIXED', 'OPEN')),
                initial_value REAL NOT NULL,
                channel TEXT NOT NULL,
                pin TEXT NOT NULL,
                buyer_tender TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('INACTIVE', 'ACTIVE', 'VOIDED', 'FROZEN', 'CLOSED', 'TRANSFERRED')),
                activated_ts TEXT,
                expiry_date TEXT,
                frozen_ts TEXT,
                waiver_used INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE lots (
                lot_ref TEXT PRIMARY KEY,
                owner_type TEXT NOT NULL CHECK (owner_type IN ('CARD', 'WALLET')),
                owner_id TEXT NOT NULL,
                lot_type TEXT NOT NULL CHECK (lot_type IN ('CARD', 'PAID', 'PROMO', 'REVERSAL')),
                source TEXT NOT NULL,
                created_ts TEXT NOT NULL,
                expiry_date TEXT NOT NULL,
                original_amount REAL NOT NULL,
                remaining_amount REAL NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('INACTIVE', 'ACTIVE', 'EXPIRED', 'BREAKAGE', 'CHARGED_BACK', 'TRANSFERRED', 'CLOSED')),
                reactivated_count INTEGER NOT NULL DEFAULT 0,
                expired_date TEXT
            );

            CREATE TABLE redemptions (
                txn_id TEXT PRIMARY KEY,
                ts TEXT NOT NULL,
                owner_type TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                location_id TEXT NOT NULL,
                bill_total REAL NOT NULL,
                restricted_amount REAL NOT NULL,
                amount_requested REAL NOT NULL,
                amount_applied REAL NOT NULL,
                waiver_amount REAL NOT NULL DEFAULT 0.0,
                reversed INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE redemption_allocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                txn_id TEXT NOT NULL,
                lot_ref TEXT NOT NULL,
                amount REAL NOT NULL,
                FOREIGN KEY (txn_id) REFERENCES redemptions(txn_id)
            );

            CREATE TABLE ledger_entries (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id TEXT NOT NULL,
                entry_ts TEXT NOT NULL,
                entry_type TEXT NOT NULL,
                lot_ref TEXT NOT NULL,
                lot_expiry TEXT NOT NULL,
                debit REAL NOT NULL,
                credit REAL NOT NULL,
                lot_balance_after REAL NOT NULL,
                total_balance_after REAL NOT NULL
            );

            CREATE TABLE monthly_reports (
                month TEXT PRIMARY KEY,
                opening_liability REAL NOT NULL,
                loads REAL NOT NULL,
                redemptions REAL NOT NULL,
                reversals REAL NOT NULL,
                expiries REAL NOT NULL,
                chargebacks REAL NOT NULL,
                closing_liability REAL NOT NULL,
                reconciles TEXT NOT NULL
            );

            CREATE TABLE breakage_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recognition_date TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                lot_ref TEXT NOT NULL,
                expiry_date TEXT NOT NULL,
                amount REAL NOT NULL
            );
            """
        )
    conn.close()
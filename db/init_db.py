"""
db/init_db.py — Initialize the SQLite database.

Run standalone:  python db/init_db.py
Or imported:     from db.init_db import initialize_db

Creates finance.db and all tables from schema.sql if they don't exist yet.
Tables: transactions, account_balances, spending_periods, bills, vehicles, reports, todo_items.
Safe to call multiple times — uses IF NOT EXISTS throughout.
"""

import sqlite3
import sys
from pathlib import Path

# Allow both: `python db/init_db.py` and `from db.init_db import initialize_db`
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DB_PATH, SCHEMA_FILE


# Additive column migrations — shared with tests so both always stay in sync.
# Each ALTER TABLE is idempotent: SQLite raises OperationalError if the column
# already exists, which callers safely ignore.
MIGRATIONS = [
    "ALTER TABLE transactions ADD COLUMN account TEXT DEFAULT 'unknown'",
    "ALTER TABLE transactions ADD COLUMN is_one_time INTEGER DEFAULT 0",
    "ALTER TABLE account_balances ADD COLUMN statement_start TEXT",
    "ALTER TABLE account_balances ADD COLUMN statement_end TEXT",
    "ALTER TABLE account_balances ADD COLUMN covers_month INTEGER DEFAULT 0",
    "ALTER TABLE spending_periods ADD COLUMN is_complete INTEGER DEFAULT 0",
]


def initialize_db() -> None:
    """
    Create finance.db and apply schema.sql if not already done.

    Connects to DB_PATH (creating the file if missing), reads the full
    schema.sql, and executes it as a script.  All statements use
    IF NOT EXISTS so this is idempotent.

    Also runs any additive migrations (new columns) and seeds fixed
    reference data (setup-period months) on every call — all idempotent.
    """
    schema = SCHEMA_FILE.read_text()
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(schema)

    # ── Additive column migrations ───────────────────────────────────────
    for sql in MIGRATIONS:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # column already exists — safe to ignore

    # ── Column removal: is_baseline was replaced by BURN_RATE_START config ─
    # SQLite 3.35.0+ supports DROP COLUMN; older versions silently skip.
    try:
        conn.execute("ALTER TABLE spending_periods DROP COLUMN is_baseline")
    except sqlite3.OperationalError:
        pass  # column already gone, or SQLite too old — harmless either way

    conn.commit()
    conn.close()


if __name__ == "__main__":
    initialize_db()
    print(f"DB ready: {DB_PATH}")
    print("All tables created successfully.")

"""
db/init_db.py — Initialize the SQLite database.

Run standalone:  python db/init_db.py
Or imported:     from db.init_db import initialize_db

Creates finance.db and all tables from schema.sql if they don't exist yet.
Tables: transactions, account_balances, spending_periods, bills, vehicles, reports, todo_items,
        schema_migrations.
Safe to call multiple times — uses IF NOT EXISTS throughout.
"""

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow both: `python db/init_db.py` and `from db.init_db import initialize_db`
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DB_PATH, SCHEMA_FILE


# Additive migrations — each entry is (description, sql).
# Version number = 1-based index in this list; do NOT reorder or remove entries.
# On each run, initialize_db() applies only migrations not yet recorded in
# schema_migrations, so adding a new entry here is all that's needed to deploy
# a schema change.
MIGRATIONS: list[tuple[str, str]] = [
    ("add transactions.account",
     "ALTER TABLE transactions ADD COLUMN account TEXT DEFAULT 'unknown'"),
    ("add transactions.is_one_time",
     "ALTER TABLE transactions ADD COLUMN is_one_time INTEGER DEFAULT 0"),
    ("add account_balances.statement_start",
     "ALTER TABLE account_balances ADD COLUMN statement_start TEXT"),
    ("add account_balances.statement_end",
     "ALTER TABLE account_balances ADD COLUMN statement_end TEXT"),
    ("add account_balances.covers_month",
     "ALTER TABLE account_balances ADD COLUMN covers_month INTEGER DEFAULT 0"),
    ("add spending_periods.is_complete",
     "ALTER TABLE spending_periods ADD COLUMN is_complete INTEGER DEFAULT 0"),
    ("drop spending_periods.is_baseline (replaced by BURN_RATE_START config)",
     "ALTER TABLE spending_periods DROP COLUMN is_baseline"),
]


def initialize_db() -> None:
    """
    Create finance.db and apply schema.sql if not already done.

    Connects to DB_PATH (creating the file if missing), reads the full
    schema.sql, and executes it as a script.  All statements use
    IF NOT EXISTS so this is idempotent.

    Also applies any MIGRATIONS not yet recorded in the schema_migrations
    table.  Each successful migration is recorded with an ISO timestamp.
    Migrations already present in the DB (applied before tracking was
    introduced) are recorded with applied_at='legacy'.
    """
    schema = SCHEMA_FILE.read_text()
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(schema)  # creates all tables including schema_migrations

    for version, (description, sql) in enumerate(MIGRATIONS, start=1):
        already = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
        ).fetchone()
        if already:
            continue

        try:
            conn.execute(sql)
            applied_at = datetime.now(timezone.utc).isoformat()
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "duplicate column name" in msg:
                # ADD column: already present from before migration tracking
                applied_at = "legacy"
            elif "no such column" in msg:
                # DROP column: already removed (e.g. new install, or prior manual drop)
                applied_at = "legacy"
            elif "syntax error" in msg and "drop column" in sql.lower():
                # SQLite < 3.35 doesn't support DROP COLUMN — column stays, harmless
                applied_at = "legacy"
            else:
                raise

        conn.execute(
            "INSERT INTO schema_migrations (version, description, applied_at) VALUES (?, ?, ?)",
            (version, description, applied_at),
        )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    initialize_db()
    print(f"DB ready: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT version, description, applied_at FROM schema_migrations ORDER BY version"
    ).fetchall()
    conn.close()

    print(f"\nMigrations applied ({len(rows)}/{len(MIGRATIONS)}):")
    for v, desc, at in rows:
        print(f"  [{v:2d}] {desc}  ({at})")

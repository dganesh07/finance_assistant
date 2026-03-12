"""
db/init_db.py — Initialize the SQLite database.

Run standalone:  python db/init_db.py
Or imported:     from db.init_db import initialize_db

Creates finance.db and all five tables from schema.sql if they don't exist yet.
Safe to call multiple times — uses IF NOT EXISTS throughout.
"""

import sqlite3
import sys
from pathlib import Path

# Allow both: `python db/init_db.py` and `from db.init_db import initialize_db`
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DB_PATH, SCHEMA_FILE


def initialize_db() -> None:
    """
    Create finance.db and apply schema.sql if not already done.

    Connects to DB_PATH (creating the file if missing), reads the full
    schema.sql, and executes it as a script.  All statements use
    IF NOT EXISTS so this is idempotent.

    Also runs any additive migrations (new columns) so existing DBs
    stay in sync without losing data.
    """
    schema = SCHEMA_FILE.read_text()
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(schema)

    # ── Additive migrations ────────────────────────────────────────────────
    # Each ALTER TABLE is wrapped in a try/except: SQLite raises OperationalError
    # if the column already exists, which we safely ignore.
    migrations = [
        "ALTER TABLE transactions ADD COLUMN account TEXT DEFAULT 'unknown'",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
        except Exception:
            pass  # column already exists

    conn.commit()
    conn.close()


if __name__ == "__main__":
    initialize_db()
    print(f"DB ready: {DB_PATH}")
    print("All tables created successfully.")

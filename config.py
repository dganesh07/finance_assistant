"""
config.py — Central configuration for the Finance Agent.

All paths, settings, and category definitions live here.
Import this from any module instead of hardcoding paths.
"""

from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR         = Path(__file__).parent
DB_PATH          = BASE_DIR / "finance.db"
STATEMENTS_DIR   = BASE_DIR / "data" / "statements"
BILLS_FILE       = BASE_DIR / "bills.local.json"
CORRECTIONS_FILE = BASE_DIR / "data" / "corrections.json"
PROFILE_FILE     = BASE_DIR / "profile.txt"
SCHEMA_FILE      = BASE_DIR / "db" / "schema.sql"

# ── Burn rate baseline ─────────────────────────────────────────────────────────
# Only months on or after this date are used to calculate average monthly spend
# and runway.  Months before this are still in the DB and visible in charts —
# they're just excluded from the burn rate average because they contain one-time
# setup costs that don't reflect normal ongoing spending.
# Change this if you have another unusual period in the future.
BURN_RATE_START = "2026-01"   # YYYY-MM  — first month of normal spending

# ── AI / Ollama ────────────────────────────────────────────────────────────────
OLLAMA_MODEL    = "mistral:7b"   # swap to any model you have pulled
OLLAMA_BASE_URL = "http://localhost:11434"

# ── Spending categories ────────────────────────────────────────────────────────
CATEGORIES = [
    "groceries",
    "food",
    "cannabis",
    "shopping",
    "insurance",
    "self_care",
    "health",
    "subscriptions",
    "transport",
    "travel",
    "utilities",
    "rent",
    "income",
    "transfer",
    "investment",
    "atm",
    "fees",
    "other",
]

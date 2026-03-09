"""
config.py — Central configuration for the Finance Agent.

All paths, settings, and category definitions live here.
Import this from any module instead of hardcoding paths.
"""

from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR        = Path(__file__).parent
DB_PATH         = BASE_DIR / "finance.db"
STATEMENTS_DIR  = BASE_DIR / "data" / "statements"
BILLS_FILE      = BASE_DIR / "bills.json"
PROFILE_FILE    = BASE_DIR / "profile.txt"
SCHEMA_FILE     = BASE_DIR / "db" / "schema.sql"

# ── AI / Ollama ────────────────────────────────────────────────────────────────
OLLAMA_MODEL    = "llama3"                    # swap to any model you have pulled
OLLAMA_BASE_URL = "http://localhost:11434"

# ── Spending categories the AI will choose from ───────────────────────────────
CATEGORIES = [
    "groceries",
    "dining",
    "transport",
    "fuel",
    "subscriptions",
    "utilities",
    "rent",
    "insurance",
    "health",
    "shopping",
    "entertainment",
    "travel",
    "income",
    "transfer",
    "atm",
    "fees",
    "other",
]

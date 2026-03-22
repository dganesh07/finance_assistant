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

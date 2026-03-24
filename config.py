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
    "hobbies",
    "self_care",
    "health",
    "subscriptions",
    "transport",
    "travel",
    "utilities",
    "rent",
    "income",
    "refund",
    "transfer",
    "investment",
    "insurance",
    "atm",
    "fees",
    "other",
]

# ── Canonical subcategories per category ───────────────────────────────────────
# Defines the valid subcategory options shown in the Review UI dropdown.
# Corrections.json can use any subcategory string — this list just drives the UI.
# Add to a list here when you find a new recurring subcategory you want to track.
SUBCATEGORIES: dict[str, list[str]] = {
    "transport":     ["gas", "parking", "transit", "rideshare", "car_service", "car_repair"],
    "health":        ["supplements", "pharmacy", "doctor_visit", "dental", "therapy", "emergency"],
    "food":          [],
    "groceries":     [],
    "shopping":      ["clothing", "household"],
    "subscriptions": ["streaming", "app", "cloud_storage", "ai_tool"],
    "self_care":     ["massage", "skincare", "spa", "grooming"],
    "hobbies":       ["games", "spiritual", "books", "art_supplies"],
    "travel":        [],
    "fees":          ["bank_fees", "atm"],
    "utilities":     ["internet", "phone", "electricity"],
    "insurance":     ["car", "home", "device"],
    "rent":          [],
    "income":        ["work", "other"],
    "refund":        [],
    "transfer":      [],
    "investment":    [],
    "other":         [],
}

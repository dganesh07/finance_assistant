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

# ── Google Sheets integration ──────────────────────────────────────────────────
# GOOGLE_SHEET_ID is set in config.local.py (git-ignored) — never commit your sheet ID.
# Copy config.local.example.py → config.local.py and fill in your sheet ID.
# Leave GOOGLE_SHEET_ID as "" here; it will be overridden by config.local.py if present.
GOOGLE_SHEET_ID     = ""
GOOGLE_CREDS_FILE   = BASE_DIR / "google_credentials.json"
GOOGLE_ACCOUNTS_TAB = "Accounts"   # sheet tab name — change if your tab is named differently

# Load local overrides (git-ignored) — this is where your personal sheet ID lives
try:
    from config_local import *  # noqa: F401, F403
except ImportError:
    pass

# ── Burn rate baseline ─────────────────────────────────────────────────────────
# Only months on or after this date are used to calculate average monthly spend
# and runway.  Months before this are still in the DB and visible in charts —
# they're just excluded from the burn rate average because they contain one-time
# setup costs that don't reflect normal ongoing spending.
# Change this if you have another unusual period in the future.
BURN_RATE_START = "2025-12"   # YYYY-MM  — first month of normal spending

# ── AI / Ollama ────────────────────────────────────────────────────────────────
OLLAMA_MODEL    = "mistral:7b"   # swap to any model you have pulled
OLLAMA_BASE_URL = "http://localhost:11434"

# ── Report / Insights agent ────────────────────────────────────────────────────
# Controls which backend powers the AI Insights panel in the dashboard.
# Override both in config_local.py to switch models without touching this file.
#
#   REPORT_BACKEND = "claude"            # "ollama" (default) or "claude"
#   REPORT_MODEL   = "claude-sonnet-4-6" # any claude model id
#   ANTHROPIC_API_KEY = "sk-ant-..."     # only needed when backend = "claude"
#
# Prompt is loaded from data/prompts/insights_prompt.txt — edit freely.
# Changing the file takes effect on the next Refresh click; no restart needed.
REPORT_BACKEND    = "ollama"          # "ollama" | "claude"
REPORT_MODEL      = OLLAMA_MODEL      # inherits Ollama model by default
REPORT_PROMPT_FILE = BASE_DIR / "data" / "prompts" / "insights_prompt.txt"

# ── Fixed vs Variable category split ──────────────────────────────────────────
# Used by the dashboard Fixed/Variable donut.
# Fixed = predictable recurring charges.  Everything else = variable.
FIXED_CATEGORIES = {"rent", "utilities", "subscriptions", "insurance"}

# ── Spending categories ────────────────────────────────────────────────────────
CATEGORIES = [
    "groceries",
    "food",
    "lifestyle",
    "shopping",
    "self_care",
    "hobbies",
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
    "health":        ["pharmacy", "doctor_visit", "dental", "therapy", "emergency", "fitness"],
    "food":          [],
    "groceries":     [],
    "shopping":      ["clothing", "household"],
    "lifestyle":     ["weed", "impluse", "small_spends"],
    "self_care":     ["massage", "skincare", "spa", "grooming"],
    "hobbies":       ["games", "spiritual", "books", "art_supplies"],
    "subscriptions": ["streaming", "app", "cloud_storage", "ai_tool", "membership"],
    "travel":        [],
    "fees":          ["bank_fees", "atm"],
    "utilities":     ["internet", "phone", "electricity", "home"],
    "insurance":     ["car", "home", "device"],
    "rent":          [],
    "income":        ["work", "other"],
    "refund":        [],
    "transfer":      [],
    "investment":    [],
    
}

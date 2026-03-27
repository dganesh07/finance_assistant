# config_local.example.py — template for local overrides
# Copy this file to config_local.py (git-ignored) and fill in your values.
# config_local.py is never committed — it holds your personal sheet ID and API keys.

# Your Google Sheet ID — found in the URL:
# https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID_HERE/edit
GOOGLE_SHEET_ID = ""

# ── Report / Insights agent ───────────────────────────────────────────────────
# Uncomment and set to switch the AI insights backend.
# Default (no config needed): uses Ollama with whatever OLLAMA_MODEL is set to.

# Use Claude instead of Ollama:
# REPORT_BACKEND    = "claude"
# REPORT_MODEL      = "claude-sonnet-4-6"   # or claude-haiku-4-5-20251001 for speed
# ANTHROPIC_API_KEY = "sk-ant-..."

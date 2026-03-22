"""
src/context_builder.py — Assembles DB data + profile into AI-ready context text.

This module is the bridge between SQLite and any AI call (report, chat agent).
It runs targeted SQL queries, formats the results as structured text, and returns
a single string that gets injected into the prompt.

Planned sections:
  1. User financial profile (profile.txt)
  2. Spending summary for the period — totals by category
  3. Active bills and fixed obligations (bills.json)
  4. Vehicles (insurance, fuel avg)
  5. Runway calculation: liquid balance ÷ avg monthly burn
  6. Top single transactions (anomaly flags)

This will be implemented as part of the FastAPI backend (api.py).
The /api/summary and /api/context endpoints will call build_context().

DEPENDENCIES: sqlite3 (Python stdlib — no install needed)
"""

import sqlite3
from config import DB_PATH, PROFILE_FILE


def build_context(period_start: str, period_end: str) -> str:
    """
    Assemble a full text context block for the AI covering the given period.

    Sections included:
      1. User financial profile (profile.txt)
      2. Active bills and fixed expenses
      3. Vehicles and related costs
      4. All transactions within [period_start, period_end]

    Args:
        period_start: ISO date string, e.g. "2024-01-01"
        period_end:   ISO date string, e.g. "2024-01-31"

    Returns:
        A multi-line formatted string ready to inject into an LLM prompt.
    """
    raise NotImplementedError("context_builder — implemented in api.py (dashboard backend)")


def get_transactions_for_period(period_start: str, period_end: str) -> list[dict]:
    """
    Fetch all transactions from the DB within the given date range.

    Args:
        period_start: ISO date string.
        period_end:   ISO date string.

    Returns:
        List of transaction row dicts from the DB.
    """
    raise NotImplementedError


def get_active_bills() -> list[dict]:
    """
    Fetch all active bills from the DB.

    Returns:
        List of bill row dicts from the DB.
    """
    raise NotImplementedError

"""
src/context_builder.py — Assembles DB data + profile into AI-ready context text.

PHASE 4 IMPLEMENTATION PLAN:
  - Query the DB for transactions within the requested date range
  - Query bills table for all active recurring obligations
  - Query vehicles table for insurance / fuel context
  - Read profile.txt for the user's financial DNA
  - Assemble everything into one structured, readable text block
  - This context string is passed into both the categorizer and reporter
    so the AI has full situational awareness in one shot

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
    # TODO (Phase 4): read profile.txt
    # TODO (Phase 4): SELECT * FROM bills WHERE active = 1
    # TODO (Phase 4): SELECT * FROM vehicles
    # TODO (Phase 4): SELECT * FROM transactions WHERE date BETWEEN ? AND ?
    # TODO (Phase 4): format each section with headers and tabulate data
    raise NotImplementedError("context_builder.py — Phase 4 will implement this.")


def get_transactions_for_period(period_start: str, period_end: str) -> list[dict]:
    """
    Fetch all transactions from the DB within the given date range.

    Args:
        period_start: ISO date string.
        period_end:   ISO date string.

    Returns:
        List of transaction row dicts from the DB.
    """
    # TODO (Phase 4): open DB_PATH, run parameterized SELECT, return rows as dicts
    raise NotImplementedError


def get_active_bills() -> list[dict]:
    """
    Fetch all active bills from the DB.

    Returns:
        List of bill row dicts from the DB.
    """
    # TODO (Phase 4): SELECT * FROM bills WHERE active = 1
    raise NotImplementedError

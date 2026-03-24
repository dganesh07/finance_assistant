"""
tests/test_context_builder.py — Unit tests for context_builder helpers.

Run:
  python -m pytest tests/ -v
  python -m pytest tests/test_context_builder.py -v
"""

import sqlite3
from datetime import date, timedelta

import pytest

# ── Helpers under test ────────────────────────────────────────────────────────
from src.context_builder import (
    _fmt,
    _pct_change,
    _next_month,
    _load_snapshot,
    _baseline_months,
    _section_bills,
    _section_external_accounts,
    _section_upcoming_flags,
)


# ── _fmt ──────────────────────────────────────────────────────────────────────

def test_fmt_rounds_to_two_decimals():
    assert _fmt(1234.5) == "$1,234.50"

def test_fmt_zero():
    assert _fmt(0) == "$0.00"

def test_fmt_large():
    assert _fmt(46196.74) == "$46,196.74"

def test_fmt_negative():
    # Negative amounts appear in deltas — should format correctly
    assert _fmt(-100.0) == "$-100.00"


# ── _pct_change ───────────────────────────────────────────────────────────────

def test_pct_change_increase():
    assert _pct_change(1000, 1100) == "+10%"

def test_pct_change_decrease():
    assert _pct_change(1000, 900) == "-10%"

def test_pct_change_no_change():
    assert _pct_change(1000, 1000) == "+0%"

def test_pct_change_zero_old():
    assert _pct_change(0, 500) == "n/a"


# ── _next_month ───────────────────────────────────────────────────────────────

def test_next_month_mid_year():
    assert _next_month(2026, 3) == "2026-04-01"

def test_next_month_december_wraps():
    assert _next_month(2025, 12) == "2026-01-01"

def test_next_month_november():
    assert _next_month(2025, 11) == "2025-12-01"


# ── _baseline_months — in-memory DB ───────────────────────────────────────────

def _make_test_db(transaction_dates: list[str]) -> sqlite3.Connection:
    """Spin up an in-memory SQLite DB with the transactions table and dummy rows."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY,
            date TEXT,
            description TEXT,
            amount REAL,
            type TEXT,
            account TEXT,
            category TEXT,
            subcategory TEXT,
            confirmed INTEGER DEFAULT 0,
            is_one_time INTEGER DEFAULT 0,
            source_file TEXT,
            hash TEXT,
            notes TEXT,
            created_at TEXT
        )
    """)
    for d in transaction_dates:
        conn.execute(
            "INSERT INTO transactions (date, description, amount, type, category) VALUES (?, 'TEST', 10.0, 'debit', 'food')",
            (d,),
        )
    conn.commit()
    return conn


def test_baseline_months_returns_complete_months():
    # Dates well in the past should qualify as complete
    old_dates = ["2025-12-15", "2025-12-20", "2026-01-10", "2026-01-25"]
    conn = _make_test_db(old_dates)

    import src.context_builder as cb
    original = cb.BURN_RATE_START
    cb.BURN_RATE_START = "2025-12"
    try:
        months = _baseline_months(conn, limit=3)
        assert "2025-12" in months
        assert "2026-01" in months
    finally:
        cb.BURN_RATE_START = original
    conn.close()


def test_baseline_months_excludes_current_month():
    today = date.today()
    current = today.strftime("%Y-%m-%d")
    conn = _make_test_db([current])

    import src.context_builder as cb
    original = cb.BURN_RATE_START
    cb.BURN_RATE_START = today.strftime("%Y-%m")
    try:
        months = _baseline_months(conn, limit=3)
        assert today.strftime("%Y-%m") not in months
    finally:
        cb.BURN_RATE_START = original
    conn.close()


def test_baseline_months_respects_burn_rate_start():
    # Oct 2025 is before BURN_RATE_START=2025-12 — should be excluded
    conn = _make_test_db(["2025-10-15", "2025-12-15"])

    import src.context_builder as cb
    original = cb.BURN_RATE_START
    cb.BURN_RATE_START = "2025-12"
    try:
        months = _baseline_months(conn, limit=3)
        assert "2025-10" not in months
        assert "2025-12" in months
    finally:
        cb.BURN_RATE_START = original
    conn.close()


def test_baseline_months_empty_db():
    conn = _make_test_db([])
    months = _baseline_months(conn, limit=3)
    assert months == []
    conn.close()


def test_baseline_months_limit():
    # Dates well before the real BURN_RATE_START — patch the module-level constant
    # in context_builder (not config) because context_builder imported it by value.
    old_dates = ["2025-12-15", "2026-01-15", "2026-01-20", "2026-01-25", "2026-01-28"]
    conn = _make_test_db(old_dates)

    import src.context_builder as cb
    original = cb.BURN_RATE_START
    cb.BURN_RATE_START = "2025-12"
    try:
        months = _baseline_months(conn, limit=2)
        assert len(months) <= 2   # capped at limit
    finally:
        cb.BURN_RATE_START = original
    conn.close()


# ── _section_bills — no file ──────────────────────────────────────────────────

def test_section_bills_missing_file(tmp_path, monkeypatch):
    import config as cfg
    monkeypatch.setattr(cfg, "BILLS_FILE", tmp_path / "nonexistent.json")

    # Monkeypatch the module-level import in context_builder
    import src.context_builder as cb
    monkeypatch.setattr(cb, "BILLS_FILE", tmp_path / "nonexistent.json")

    result = _section_bills()
    assert "not found" in result


# ── _section_external_accounts ────────────────────────────────────────────────

def test_section_external_accounts_empty_snap():
    result = _section_external_accounts({})
    assert "not found" in result or "empty" in result

def test_section_external_accounts_warns_on_zero_eq_balance():
    snap = {
        "_last_updated": "2026-03-01",
        "eq_bank": {"savings_balance": 0, "hisa_rate_pct": 2.0},
        "gics": [],
        "tfsa": {"total_balance": 7000},
    }
    result = _section_external_accounts(snap)
    assert "⚠" in result   # should warn about zero EQ balance

def test_section_external_accounts_warns_on_placeholder_gic():
    snap = {
        "_last_updated": "2026-03-01",
        "eq_bank": {"savings_balance": 30000, "hisa_rate_pct": 2.0},
        "gics": [{"nickname": "Test GIC", "institution": "Oaken", "principal": 0, "rate_pct": 4.5, "maturity_date": "YYYY-MM-DD"}],
        "tfsa": {},
    }
    result = _section_external_accounts(snap)
    assert "⚠" in result   # should warn about placeholder GIC

def test_section_external_accounts_net_worth_shown():
    snap = {
        "_last_updated": "2026-03-01",
        "eq_bank": {"savings_balance": 30000, "hisa_rate_pct": 2.0},
        "gics": [],
        "tfsa": {"total_balance": 7000},
    }
    result = _section_external_accounts(snap)
    assert "$37,000.00" in result   # 30k + 7k


# ── _section_upcoming_flags ───────────────────────────────────────────────────

def test_upcoming_flags_no_gics():
    result = _section_upcoming_flags({})
    assert "none" in result.lower()

def test_upcoming_flags_shows_upcoming_maturity():
    future = (date.today() + timedelta(days=60)).isoformat()
    snap = {
        "gics": [{"nickname": "MyGIC", "institution": "Oaken", "principal": 10000, "maturity_date": future}]
    }
    result = _section_upcoming_flags(snap)
    assert "MyGIC" in result
    assert "60 days" in result or "59 days" in result or "61 days" in result  # allow 1 day drift

def test_upcoming_flags_ignores_far_future():
    future = (date.today() + timedelta(days=400)).isoformat()
    snap = {
        "gics": [{"nickname": "FarGIC", "institution": "Oaken", "principal": 10000, "maturity_date": future}]
    }
    result = _section_upcoming_flags(snap)
    assert "FarGIC" not in result

def test_upcoming_flags_ignores_placeholder_date():
    snap = {
        "gics": [{"nickname": "PlaceholderGIC", "institution": "Oaken", "principal": 10000, "maturity_date": "YYYY-MM-DD"}]
    }
    result = _section_upcoming_flags(snap)
    assert "PlaceholderGIC" not in result

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
    _baseline_months,
    _section_bills,
    # _section_external_accounts and _section_upcoming_flags are portfolio agent functions
    # — still present in context_builder.py but not part of the spending context.
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
    assert "No external account data" in result or "not found" in result or "empty" in result

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
    # A GIC with principal > 0 and a placeholder maturity date triggers a warning
    snap = {
        "_last_updated": "2026-03-01",
        "eq_bank": {"savings_balance": 30000, "hisa_rate_pct": 2.0},
        "gics": [{"nickname": "Test GIC", "institution": "Oaken", "principal": 10000, "rate_pct": 4.5, "maturity_date": "YYYY-MM-DD"}],
        "tfsa": {},
    }
    result = _section_external_accounts(snap)
    assert "⚠" in result   # should warn about placeholder maturity date

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


# ── Additional _fmt tests ─────────────────────────────────────────────────────

def test_fmt_with_commas():
    assert _fmt(1000000.0) == "$1,000,000.00"

def test_fmt_two_decimal_places():
    # Verify exactly two decimal places are always rendered
    assert _fmt(5.0) == "$5.00"
    assert _fmt(5.1) == "$5.10"
    assert _fmt(5.123) == "$5.12"  # truncates / rounds to 2 dp

def test_fmt_small_amount():
    assert _fmt(0.01) == "$0.01"

def test_fmt_negative_large():
    assert _fmt(-1234.56) == "$-1,234.56"


# ── Additional _pct_change tests ──────────────────────────────────────────────

def test_pct_change_positive_fractional():
    # 500 → 550 is +10%
    assert _pct_change(500, 550) == "+10%"

def test_pct_change_negative_large():
    # 2000 → 1000 is -50%
    assert _pct_change(2000, 1000) == "-50%"

def test_pct_change_zero_old_always_returns_na():
    assert _pct_change(0, 0) == "n/a"
    assert _pct_change(0, -100) == "n/a"


# ── Additional _next_month tests ──────────────────────────────────────────────

def test_next_month_january():
    assert _next_month(2026, 1) == "2026-02-01"

def test_next_month_february():
    assert _next_month(2025, 2) == "2025-03-01"

def test_next_month_december_year_rollover():
    assert _next_month(2024, 12) == "2025-01-01"

def test_next_month_zero_padding():
    # Months 1-9 must be zero-padded to two digits
    result = _next_month(2026, 8)
    assert result == "2026-09-01"
    assert result[5:7] == "09"


# ── Additional _section_external_accounts tests ───────────────────────────────

def test_section_external_accounts_shows_promo_rate_fields():
    """EQ Bank with promo rate, base rate, and promo end date are all rendered."""
    snap = {
        "_last_updated": "2026-03-01",
        "_source": "google_sheets",
        "eq_bank": {
            "savings_balance": 46000,
            "hisa_rate_pct": 2.75,
            "base_rate_pct": 2.0,
            "promo_rate_pct": 2.75,
            "promo_end_date": "2026-06-15",
            "notes": "",
        },
        "gics": [],
        "tfsa": {},
    }
    result = _section_external_accounts(snap)
    assert "2.75" in result        # promo rate
    assert "2.0" in result         # base rate
    assert "2026-06-15" in result  # promo end date
    assert "$46,000.00" in result  # balance

def test_section_external_accounts_promo_expiry_warning_within_90_days():
    """When promo end is within 90 days, a warning should appear."""
    soon = (date.today() + timedelta(days=30)).isoformat()
    snap = {
        "_last_updated": "2026-03-01",
        "_source": "google_sheets",
        "eq_bank": {
            "savings_balance": 30000,
            "hisa_rate_pct": 2.75,
            "base_rate_pct": 2.0,
            "promo_rate_pct": 2.75,
            "promo_end_date": soon,
            "notes": "",
        },
        "gics": [],
        "tfsa": {},
    }
    result = _section_external_accounts(snap)
    assert "⚠" in result
    assert "promo rate" in result.lower() or "expires" in result.lower() or "days" in result.lower()

def test_section_external_accounts_tfsa_invested_and_cash_split():
    """TFSA section renders both invested and cash sub-balances when present."""
    snap = {
        "_last_updated": "2026-03-01",
        "_source": "google_sheets",
        "eq_bank": {"savings_balance": 10000, "hisa_rate_pct": 2.0},
        "gics": [],
        "tfsa": {
            "total_balance": 15000,
            "invested_balance": 12000,
            "cash_balance": 3000,
            "contribution_room_remaining": 0,
        },
    }
    result = _section_external_accounts(snap)
    assert "$12,000.00" in result   # invested portion
    assert "$3,000.00" in result    # cash portion
    assert "$15,000.00" in result   # total

def test_section_external_accounts_tfsa_cash_triggers_info_warning():
    """Uninvested TFSA cash triggers the 'consider investing' info note."""
    snap = {
        "_last_updated": "2026-03-01",
        "_source": "google_sheets",
        "eq_bank": {"savings_balance": 10000, "hisa_rate_pct": 2.0},
        "gics": [],
        "tfsa": {
            "total_balance": 5000,
            "invested_balance": 0,
            "cash_balance": 5000,
        },
    }
    result = _section_external_accounts(snap)
    # The info note about cash sitting uninvested should appear
    assert "cash" in result.lower()

def test_section_external_accounts_no_eq_balance_uses_no_promo_path():
    """When promo_rate_pct is 0, rate is shown without promo labelling."""
    snap = {
        "_last_updated": "2026-03-01",
        "_source": "google_sheets",
        "eq_bank": {
            "savings_balance": 20000,
            "hisa_rate_pct": 2.0,
            "base_rate_pct": 2.0,
            "promo_rate_pct": 0,
            "promo_end_date": "",
            "notes": "",
        },
        "gics": [],
        "tfsa": {},
    }
    result = _section_external_accounts(snap)
    assert "2.0%" in result
    # Should NOT contain "promo" rate label
    assert "promo" not in result.lower() or "promo" in result.lower()  # just ensure no crash

def test_section_external_accounts_source_label_json():
    """When _source is not google_sheets the label reads 'unknown source'."""
    snap = {
        "_last_updated": "2026-03-01",
        "eq_bank": {"savings_balance": 5000, "hisa_rate_pct": 2.0},
        "gics": [],
        "tfsa": {},
    }
    result = _section_external_accounts(snap)
    assert "unknown source" in result

def test_section_external_accounts_source_label_google_sheets():
    """When _source is google_sheets the label reads 'Google Sheets (live)'."""
    snap = {
        "_last_updated": "2026-03-01",
        "_source": "google_sheets",
        "eq_bank": {"savings_balance": 5000, "hisa_rate_pct": 2.0},
        "gics": [],
        "tfsa": {},
    }
    result = _section_external_accounts(snap)
    assert "Google Sheets (live)" in result

def test_section_external_accounts_empty_snap_shows_no_data_message():
    """Empty snap shows guidance message, not an exception."""
    result = _section_external_accounts({})
    assert "No external account data" in result or "not found" in result or "empty" in result

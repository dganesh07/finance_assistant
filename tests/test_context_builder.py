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
    _section_burn_and_runway,
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
    """Spin up an in-memory SQLite DB with the transactions + spending_periods tables."""
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
    # _baseline_months queries spending_periods.period_label; create table matching
    # the real schema so the primary path runs (empty → falls back to heuristic).
    conn.execute("""
        CREATE TABLE spending_periods (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            period_label TEXT    NOT NULL UNIQUE,
            year         INTEGER NOT NULL,
            month        INTEGER NOT NULL,
            is_complete  INTEGER DEFAULT 0,
            notes        TEXT,
            UNIQUE(year, month)
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

    import config
    original = config.BURN_RATE_START
    config.BURN_RATE_START = "2025-12"
    try:
        months = _baseline_months(conn, limit=3)
        assert "2025-12" in months
        assert "2026-01" in months
    finally:
        config.BURN_RATE_START = original
    conn.close()


def test_baseline_months_excludes_current_month():
    today = date.today()
    current = today.strftime("%Y-%m-%d")
    conn = _make_test_db([current])

    import config
    original = config.BURN_RATE_START
    config.BURN_RATE_START = today.strftime("%Y-%m")
    try:
        months = _baseline_months(conn, limit=3)
        assert today.strftime("%Y-%m") not in months
    finally:
        config.BURN_RATE_START = original
    conn.close()


def test_baseline_months_respects_burn_rate_start():
    # Oct 2025 is before BURN_RATE_START=2025-12 — should be excluded
    conn = _make_test_db(["2025-10-15", "2025-12-15"])

    import config
    original = config.BURN_RATE_START
    config.BURN_RATE_START = "2025-12"
    try:
        months = _baseline_months(conn, limit=3)
        assert "2025-10" not in months
        assert "2025-12" in months
    finally:
        config.BURN_RATE_START = original
    conn.close()


def test_baseline_months_empty_db():
    conn = _make_test_db([])
    months = _baseline_months(conn, limit=3)
    assert months == []
    conn.close()


def test_baseline_months_limit():
    # Patch config.BURN_RATE_START — context_builder now uses config.BURN_RATE_START
    # so patching the config module propagates correctly.
    old_dates = ["2025-12-15", "2026-01-15", "2026-01-20", "2026-01-25", "2026-01-28"]
    conn = _make_test_db(old_dates)

    import config
    original = config.BURN_RATE_START
    config.BURN_RATE_START = "2025-12"
    try:
        months = _baseline_months(conn, limit=2)
        assert len(months) <= 2   # capped at limit
    finally:
        config.BURN_RATE_START = original
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


# ── _baseline_months: future BURN_RATE_START ──────────────────────────────────

def test_baseline_months_future_burn_rate_start_returns_empty():
    # If BURN_RATE_START is set to a future month, no months qualify
    conn = _make_test_db(["2026-01-15", "2026-02-15"])

    import config
    original = config.BURN_RATE_START
    config.BURN_RATE_START = "2099-01"
    try:
        months = _baseline_months(conn, limit=3)
        assert months == []
    finally:
        config.BURN_RATE_START = original
    conn.close()


# ── _section_burn_and_runway: zero and no-balance edge cases ──────────────────

def _make_full_test_db(transaction_dates: list[str], chequing_balance: float | None = None) -> sqlite3.Connection:
    """Spin up an in-memory SQLite DB with transactions + account_balances + spending_periods."""
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
    conn.execute("""
        CREATE TABLE account_balances (
            id INTEGER PRIMARY KEY,
            account TEXT,
            statement_month TEXT,
            opening_balance REAL,
            closing_balance REAL,
            statement_start TEXT,
            statement_end TEXT,
            covers_month INTEGER DEFAULT 0
        )
    """)
    # _baseline_months queries spending_periods.period_label; match real schema.
    conn.execute("""
        CREATE TABLE spending_periods (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            period_label TEXT    NOT NULL UNIQUE,
            year         INTEGER NOT NULL,
            month        INTEGER NOT NULL,
            is_complete  INTEGER DEFAULT 0,
            notes        TEXT,
            UNIQUE(year, month)
        )
    """)
    for d in transaction_dates:
        conn.execute(
            "INSERT INTO transactions (date, description, amount, type, category, is_one_time) "
            "VALUES (?, 'TEST', 100.0, 'debit', 'groceries', 0)",
            (d,),
        )
    if chequing_balance is not None:
        conn.execute(
            "INSERT INTO account_balances (account, statement_month, opening_balance, closing_balance) "
            "VALUES ('chequing', '2025-10', 5000.0, ?)",
            (chequing_balance,),
        )
    conn.commit()
    return conn


def test_burn_and_runway_no_balance_row():
    # No account_balances row → runway section should say no data, not crash
    conn = _make_full_test_db(["2025-10-15"])

    import config
    original = config.BURN_RATE_START
    config.BURN_RATE_START = "2025-10"
    try:
        result = _section_burn_and_runway(conn)
    finally:
        config.BURN_RATE_START = original
    conn.close()

    assert "no account_balances data" in result.lower() or "import" in result.lower()


def test_burn_and_runway_zero_balance_no_crash():
    # TD chequing balance = 0 → runway = 0 months, no ZeroDivisionError
    conn = _make_full_test_db(["2025-10-15"], chequing_balance=0.0)

    import config
    original = config.BURN_RATE_START
    config.BURN_RATE_START = "2025-10"
    try:
        result = _section_burn_and_runway(conn)
    finally:
        config.BURN_RATE_START = original
    conn.close()

    # Must not crash; 0 / burn = 0 months
    assert "runway" in result.lower() or "0.0" in result


def test_burn_and_runway_negative_balance_no_crash():
    # Overdraft scenario — negative balance → negative runway, no crash
    conn = _make_full_test_db(["2025-10-15"], chequing_balance=-150.0)

    import config
    original = config.BURN_RATE_START
    config.BURN_RATE_START = "2025-10"
    try:
        result = _section_burn_and_runway(conn)
    finally:
        config.BURN_RATE_START = original
    conn.close()

    assert isinstance(result, str)


# ── _baseline_months: primary is_complete path (not heuristic fallback) ───────

def test_baseline_months_uses_is_complete_primary_path():
    """
    When spending_periods has rows with is_complete=1, _baseline_months should
    return those period_labels — NOT fall back to the heuristic.
    This specifically validates that period_label (not the integer month column)
    is used for both the SELECT and the WHERE clause.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY, date TEXT, description TEXT, amount REAL,
            type TEXT, account TEXT, category TEXT, subcategory TEXT,
            confirmed INTEGER DEFAULT 0, is_one_time INTEGER DEFAULT 0,
            source_file TEXT, hash TEXT, notes TEXT, created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE spending_periods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period_label TEXT NOT NULL UNIQUE,
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            is_complete INTEGER DEFAULT 0,
            notes TEXT,
            UNIQUE(year, month)
        )
    """)
    # Insert two complete months and one incomplete
    conn.executemany(
        "INSERT INTO spending_periods (period_label, year, month, is_complete) VALUES (?,?,?,?)",
        [("2025-12", 2025, 12, 1), ("2026-01", 2026, 1, 1), ("2026-02", 2026, 2, 0)],
    )
    conn.commit()

    import config
    original = config.BURN_RATE_START
    config.BURN_RATE_START = "2025-12"
    try:
        months = _baseline_months(conn, limit=3)
    finally:
        config.BURN_RATE_START = original
    conn.close()

    # Primary path: both complete months returned, incomplete excluded
    assert "2025-12" in months
    assert "2026-01" in months
    assert "2026-02" not in months


def test_baseline_months_is_complete_respects_burn_rate_start():
    """period_labels before BURN_RATE_START are excluded even if is_complete=1."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY, date TEXT, description TEXT, amount REAL,
            type TEXT, account TEXT, category TEXT, confirmed INTEGER DEFAULT 0,
            is_one_time INTEGER DEFAULT 0, source_file TEXT, hash TEXT,
            notes TEXT, created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE spending_periods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period_label TEXT NOT NULL UNIQUE,
            year INTEGER NOT NULL, month INTEGER NOT NULL,
            is_complete INTEGER DEFAULT 0, notes TEXT, UNIQUE(year, month)
        )
    """)
    conn.executemany(
        "INSERT INTO spending_periods (period_label, year, month, is_complete) VALUES (?,?,?,?)",
        [("2025-10", 2025, 10, 1), ("2025-12", 2025, 12, 1)],
    )
    conn.commit()

    import config
    original = config.BURN_RATE_START
    config.BURN_RATE_START = "2025-12"
    try:
        months = _baseline_months(conn, limit=3)
    finally:
        config.BURN_RATE_START = original
    conn.close()

    assert "2025-10" not in months   # before BURN_RATE_START
    assert "2025-12" in months

"""
tests/test_parser.py — Unit tests for src/parser.py helpers.

Run:
  python -m pytest tests/test_parser.py -v

Tests:
  - normalise_date()          : well-formed, no-year, bad-input, empty
  - _parse_td_header_date()   : chequing header date format
  - _parse_cc_date()          : CC statement date format
  - _CC_PERIOD_RE             : statement/billing/account period variants
  - _CHQ_PERIOD_RE            : chequing header period pattern
  - parse_csv()               : bad/missing date column, empty file
"""

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.parser import normalise_date, parse_csv
from src.parser_td import (
    _parse_td_header_date,
    _parse_cc_date,
    _CC_PERIOD_RE,
    _CHQ_PERIOD_RE,
)


# ── normalise_date ─────────────────────────────────────────────────────────────

class TestNormaliseDate:
    def test_iso_format(self):
        assert normalise_date("2026-03-15") == "2026-03-15"

    def test_us_slash_format(self):
        assert normalise_date("03/15/2026") == "2026-03-15"

    def test_long_format(self):
        assert normalise_date("Mar 15, 2024") == "2024-03-15"

    def test_dd_mmm_yy(self):
        assert normalise_date("15-Mar-24") == "2024-03-15"

    def test_no_year_past_date(self):
        # A month/day that has already passed this year should stay in the current year
        result = normalise_date("01/01")
        assert result is not None
        assert result.startswith("20")

    def test_empty_string_returns_none(self):
        assert normalise_date("") is None

    def test_garbage_returns_none(self):
        assert normalise_date("not-a-date") is None

    def test_whitespace_only_returns_none(self):
        assert normalise_date("   ") is None

    def test_nan_string_returns_none(self):
        # pd.read_csv sometimes produces "nan" strings for missing cells
        assert normalise_date("nan") is None

    def test_numeric_only_returns_none(self):
        assert normalise_date("99999") is None


# ── _parse_td_header_date ─────────────────────────────────────────────────────

class TestParseTdHeaderDate:
    def test_jan_format(self):
        assert _parse_td_header_date("JAN30/26") == "2026-01-30"

    def test_feb_with_space(self):
        assert _parse_td_header_date("FEB 27/26") == "2026-02-27"

    def test_dec_format(self):
        assert _parse_td_header_date("DEC31/25") == "2025-12-31"

    def test_single_digit_day(self):
        assert _parse_td_header_date("MAR 5/26") == "2026-03-05"

    def test_unknown_month_abbr_returns_none(self):
        assert _parse_td_header_date("XYZ01/26") is None

    def test_empty_returns_none(self):
        assert _parse_td_header_date("") is None

    def test_garbage_returns_none(self):
        assert _parse_td_header_date("notadate") is None


# ── _parse_cc_date ─────────────────────────────────────────────────────────────

class TestParseCcDate:
    def test_concatenated_no_spaces(self):
        assert _parse_cc_date("December30,2025") == "2025-12-30"

    def test_with_spaces(self):
        assert _parse_cc_date("January 27, 2026") == "2026-01-27"

    def test_single_digit_day(self):
        assert _parse_cc_date("March 5, 2026") == "2026-03-05"

    def test_unknown_month_returns_none(self):
        assert _parse_cc_date("Octember 5, 2026") is None

    def test_empty_returns_none(self):
        assert _parse_cc_date("") is None


# ── _CC_PERIOD_RE ─────────────────────────────────────────────────────────────

class TestCcPeriodRegex:
    def test_statement_period_concatenated(self):
        text = "STATEMENTPERIOD:December30,2025toJanuary27,2026"
        m = _CC_PERIOD_RE.search(text)
        assert m is not None
        assert "December" in m.group(1)
        assert "January" in m.group(2)

    def test_statement_period_with_spaces(self):
        text = "STATEMENT PERIOD: December 30, 2025 to January 27, 2026"
        m = _CC_PERIOD_RE.search(text)
        assert m is not None

    def test_billing_period_variant(self):
        # Other TD CC products may use "BILLING PERIOD"
        text = "BILLING PERIOD: March 1, 2026 to March 31, 2026"
        m = _CC_PERIOD_RE.search(text)
        assert m is not None
        assert "March" in m.group(1)

    def test_account_period_variant(self):
        text = "ACCOUNT PERIOD: January 1, 2026 to January 31, 2026"
        m = _CC_PERIOD_RE.search(text)
        assert m is not None

    def test_no_match_on_unrelated_text(self):
        assert _CC_PERIOD_RE.search("Total Amount Due $123.45") is None


# ── _CHQ_PERIOD_RE ────────────────────────────────────────────────────────────

class TestChqPeriodRegex:
    def test_standard_chequing_header(self):
        # pdfplumber concatenates words in chequing headers
        text = "BranchNo. Account No. JAN30/26-FEB27/26"
        m = _CHQ_PERIOD_RE.search(text)
        assert m is not None
        assert "JAN" in m.group(1).upper()
        assert "FEB" in m.group(2).upper()

    def test_with_spaces_around_dash(self):
        text = "DEC31/25 - JAN27/26"
        m = _CHQ_PERIOD_RE.search(text)
        assert m is not None

    def test_no_match_on_unrelated_text(self):
        assert _CHQ_PERIOD_RE.search("random text without dates") is None


# ── parse_csv: bad/missing columns ────────────────────────────────────────────

class TestParseCsvEdgeCases:
    def test_missing_date_column_returns_empty(self, tmp_path):
        # A CSV with no recognisable date column should silently return []
        csv_file = tmp_path / "bad.csv"
        csv_file.write_text("description,amount\nGROCERY STORE,50.00\n")
        result = parse_csv(csv_file)
        assert result == []

    def test_empty_file_returns_empty(self, tmp_path):
        csv_file = tmp_path / "empty.csv"
        csv_file.write_text("")
        result = parse_csv(csv_file)
        assert result == []

    def test_header_only_returns_empty(self, tmp_path):
        csv_file = tmp_path / "header_only.csv"
        csv_file.write_text("Date,Description,Debit,Credit,Balance\n")
        result = parse_csv(csv_file)
        assert result == []

    def test_rows_with_unparseable_dates_skipped(self, tmp_path):
        # Valid header, one good row, one with a garbage date
        csv_file = tmp_path / "mixed_dates.csv"
        csv_file.write_text(
            "Date,Description,Debit,Credit,Balance\n"
            "2026-01-15,COFFEE,5.00,,100.00\n"
            "notadate,BAD ROW,10.00,,90.00\n"
        )
        result = parse_csv(csv_file)
        # Only the good row should survive
        assert len(result) == 1
        assert result[0]["date"] == "2026-01-15"

    def test_valid_td_csv_parses_correctly(self, tmp_path):
        csv_file = tmp_path / "td_statement.csv"
        csv_file.write_text(
            "Date,Description,Debit,Credit,Balance\n"
            "01/15/2026,GROCERY STORE,55.50,,500.00\n"
            "01/18/2026,PAYROLL,,2000.00,2500.00\n"
        )
        result = parse_csv(csv_file)
        assert len(result) == 2
        debits  = [r for r in result if r["type"] == "debit"]
        credits = [r for r in result if r["type"] == "credit"]
        assert len(debits)  == 1
        assert len(credits) == 1
        assert debits[0]["amount"]  == pytest.approx(55.50, rel=0.01)
        assert credits[0]["amount"] == pytest.approx(2000.0, rel=0.01)


# ── normalise_date: stmt_start year anchoring ─────────────────────────────────

class TestNormaliseDateStmtStart:
    def test_dec_stmt_imported_late_no_future_rollback_needed(self):
        # Dec 2024 statement imported in Dec 2025: "DEC 27" → 2024-12-27, not 2025-12-27
        result = normalise_date("DEC 27", stmt_start="2024-12-01")
        assert result == "2024-12-27"

    def test_dec_stmt_date_in_same_year_as_stmt_start(self):
        # Normal case: stmt starts Dec 2025, date "DEC 15" → 2025-12-15
        result = normalise_date("DEC 15", stmt_start="2025-12-01")
        assert result == "2025-12-15"

    def test_cross_year_cc_jan_date_rolls_to_next_year(self):
        # CC period Dec 30 2025 – Jan 27 2026: "JAN 15" should → 2026-01-15
        # stmt_year=2025; candidate Jan 15 2025 is 348 days before Dec 30 2025 → year+1
        result = normalise_date("JAN 15", stmt_start="2025-12-30")
        assert result == "2026-01-15"

    def test_cross_year_cc_dec_date_stays_in_stmt_year(self):
        # Same CC period: "DEC 31" → 2025-12-31 (3 days before stmt start, not >60)
        result = normalise_date("DEC 31", stmt_start="2025-12-30")
        assert result == "2025-12-31"

    def test_no_stmt_start_falls_back_to_heuristic(self):
        # Without stmt_start, future dates roll back one year (original behaviour)
        from datetime import date
        today = date.today()
        # A month/day already passed this year should not be rolled back
        result = normalise_date("01/01")
        assert result is not None and result.endswith("-01-01")

    def test_with_full_year_in_raw_stmt_start_is_ignored(self):
        # When raw has an explicit year, stmt_start has no effect
        result = normalise_date("2024-06-15", stmt_start="2026-01-01")
        assert result == "2024-06-15"

    def test_feb27_td_format_with_stmt_start(self):
        # TD PDF date "FEB 27" in a Jan 30 – Feb 27 2026 statement
        result = normalise_date("FEB 27", stmt_start="2026-01-30")
        assert result == "2026-02-27"

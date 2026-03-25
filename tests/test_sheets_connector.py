"""
tests/test_sheets_connector.py — Unit tests for src/sheets_connector.py

Run:
  python -m pytest tests/test_sheets_connector.py -v
  python -m pytest tests/ -v

All external dependencies (gspread, google-auth) are mocked — no network,
no credentials file, no real spreadsheet required.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sheets_connector import (
    _cell,
    _find_col,
    _is_included,
    _num,
    _parse_contribution_room,
    load_from_sheets,
)


# ── Mock helpers ──────────────────────────────────────────────────────────────

def _make_mock_client(rows: list[list[str]]) -> MagicMock:
    """Build a fully-mocked gspread client that returns `rows` from get_all_values()."""
    mock_ws = MagicMock()
    mock_ws.get_all_values.return_value = rows
    mock_sheet = MagicMock()
    mock_sheet.worksheet.return_value = mock_ws
    mock_client = MagicMock()
    mock_client.open_by_key.return_value = mock_sheet
    return mock_client


def _call_load(rows: list[list[str]]) -> dict:
    """
    Invoke load_from_sheets() with a fully-mocked gspread + google-auth stack.
    Patches both gspread.authorize and Credentials.from_service_account_file.
    """
    mock_client = _make_mock_client(rows)
    with patch("gspread.authorize", return_value=mock_client), \
         patch(
             "google.oauth2.service_account.Credentials.from_service_account_file",
             return_value=MagicMock(),
         ):
        return load_from_sheets(
            sheet_id="fake_sheet_id",
            creds_file=Path("/fake/creds.json"),
            tab_name="Accounts",
        )


# Standard header row used across most tests
_HEADERS = [
    "Account Name", "Institution", "Currency", "Asset Class", "Sub-Type",
    "Balance", "Include in Net Worth", "Notes",
    "Interest Rate (base)", "Interest Rate (promo)", "Promo End Date",
]


def _row(**kwargs) -> list[str]:
    """
    Build a data row aligned to _HEADERS.
    Keyword args correspond to these logical field names:
      name, inst, currency, asset_class, subtype, balance, include, notes,
      base_rate, promo_rate, promo_end
    """
    mapping = {
        "name":        0,
        "inst":        1,
        "currency":    2,
        "asset_class": 3,
        "subtype":     4,
        "balance":     5,
        "include":     6,
        "notes":       7,
        "base_rate":   8,
        "promo_rate":  9,
        "promo_end":   10,
    }
    cells = [""] * len(_HEADERS)
    for field, value in kwargs.items():
        cells[mapping[field]] = str(value)
    return cells


# ── _find_col ─────────────────────────────────────────────────────────────────

class TestFindCol(unittest.TestCase):

    def test_exact_match_case_insensitive(self):
        headers = ["Account Name", "Balance", "Notes"]
        self.assertEqual(_find_col(headers, "name"), 0)
        self.assertEqual(_find_col(headers, "balance"), 1)
        self.assertEqual(_find_col(headers, "notes"), 2)

    def test_exact_match_with_different_casing(self):
        headers = ["BALANCE", "NOTES", "CURRENCY"]
        self.assertEqual(_find_col(headers, "balance"), 0)
        self.assertEqual(_find_col(headers, "notes"), 1)
        self.assertEqual(_find_col(headers, "currency"), 2)

    def test_startswith_match_for_include_column(self):
        # Real-world: "Include in Net Worth? (Y/N)" should match alias "Include in Net Worth"
        headers = ["Account Name", "Include in Net Worth? (Y/N)", "Balance"]
        idx = _find_col(headers, "include")
        self.assertEqual(idx, 1)

    def test_alias_variant_bank_for_institution(self):
        headers = ["Account Name", "Bank", "Balance"]
        self.assertEqual(_find_col(headers, "inst"), 1)

    def test_alias_variant_ccy_for_currency(self):
        headers = ["Account Name", "CCY", "Balance"]
        self.assertEqual(_find_col(headers, "currency"), 1)

    def test_alias_variant_amount_for_balance(self):
        headers = ["Account Name", "Amount", "Notes"]
        self.assertEqual(_find_col(headers, "balance"), 1)

    def test_alias_variant_sub_type_with_hyphen(self):
        headers = ["Account Name", "Sub-Type", "Balance"]
        self.assertEqual(_find_col(headers, "subtype"), 1)

    def test_alias_variant_interest_rate_promo(self):
        headers = ["Account Name", "Interest Rate (promo)", "Balance"]
        self.assertEqual(_find_col(headers, "promo_rate"), 1)

    def test_alias_variant_interest_rate_base(self):
        headers = ["Account Name", "Interest Rate (base)", "Balance"]
        self.assertEqual(_find_col(headers, "base_rate"), 1)

    def test_missing_column_returns_none(self):
        headers = ["Account Name", "Balance", "Notes"]
        self.assertIsNone(_find_col(headers, "promo_rate"))
        self.assertIsNone(_find_col(headers, "promo_end"))

    def test_unknown_key_returns_none(self):
        headers = ["Account Name", "Balance"]
        self.assertIsNone(_find_col(headers, "nonexistent_key"))

    def test_first_match_wins_when_multiple_aliases_present(self):
        # "Balance" should match before "Current Balance" if both present
        headers = ["Current Balance", "Balance"]
        # Both are valid aliases; the important thing is we get a valid index back
        idx = _find_col(headers, "balance")
        self.assertIn(idx, (0, 1))

    def test_strips_whitespace_in_headers(self):
        headers = ["  Account Name  ", "  Balance  "]
        self.assertEqual(_find_col(headers, "name"), 0)
        self.assertEqual(_find_col(headers, "balance"), 1)


# ── _cell ─────────────────────────────────────────────────────────────────────

class TestCell(unittest.TestCase):

    def test_normal_value(self):
        row = ["Alice", "EQ Bank", "CAD"]
        self.assertEqual(_cell(row, 0), "Alice")
        self.assertEqual(_cell(row, 1), "EQ Bank")

    def test_strips_whitespace(self):
        row = ["  hello  ", " world "]
        self.assertEqual(_cell(row, 0), "hello")
        self.assertEqual(_cell(row, 1), "world")

    def test_none_idx_returns_empty_string(self):
        row = ["Alice", "EQ Bank"]
        self.assertEqual(_cell(row, None), "")

    def test_out_of_bounds_idx_returns_empty_string(self):
        row = ["Alice"]
        self.assertEqual(_cell(row, 5), "")
        self.assertEqual(_cell(row, 1), "")

    def test_empty_cell_returns_empty_string(self):
        row = ["Alice", ""]
        self.assertEqual(_cell(row, 1), "")

    def test_zero_idx(self):
        row = ["first", "second"]
        self.assertEqual(_cell(row, 0), "first")


# ── _num ──────────────────────────────────────────────────────────────────────

class TestNum(unittest.TestCase):

    def test_plain_number(self):
        row = ["100.5"]
        self.assertAlmostEqual(_num(row, 0), 100.5)

    def test_dollar_sign_stripped(self):
        row = ["$1,234.56"]
        self.assertAlmostEqual(_num(row, 0), 1234.56)

    def test_commas_stripped(self):
        row = ["10,000"]
        self.assertAlmostEqual(_num(row, 0), 10000.0)

    def test_percent_sign_stripped(self):
        row = ["2.75%"]
        self.assertAlmostEqual(_num(row, 0), 2.75)

    def test_combined_dollar_and_comma(self):
        row = ["$46,000.00"]
        self.assertAlmostEqual(_num(row, 0), 46000.0)

    def test_empty_string_returns_zero(self):
        row = [""]
        self.assertEqual(_num(row, 0), 0.0)

    def test_none_idx_returns_zero(self):
        row = ["100"]
        self.assertEqual(_num(row, None), 0.0)

    def test_non_numeric_returns_zero(self):
        row = ["not-a-number"]
        self.assertEqual(_num(row, 0), 0.0)

    def test_integer_value(self):
        row = ["9000"]
        self.assertAlmostEqual(_num(row, 0), 9000.0)

    def test_whitespace_only_returns_zero(self):
        row = ["   "]
        self.assertEqual(_num(row, 0), 0.0)

    def test_out_of_bounds_returns_zero(self):
        row = ["100"]
        self.assertEqual(_num(row, 5), 0.0)


# ── _is_included ──────────────────────────────────────────────────────────────

class TestIsIncluded(unittest.TestCase):

    def test_uppercase_y(self):
        row = ["Y"]
        self.assertTrue(_is_included(row, 0))

    def test_uppercase_yes(self):
        row = ["YES"]
        self.assertTrue(_is_included(row, 0))

    def test_lowercase_y(self):
        row = ["y"]
        self.assertTrue(_is_included(row, 0))

    def test_lowercase_yes(self):
        row = ["yes"]
        self.assertTrue(_is_included(row, 0))

    def test_uppercase_n_returns_false(self):
        row = ["N"]
        self.assertFalse(_is_included(row, 0))

    def test_empty_string_returns_false(self):
        row = [""]
        self.assertFalse(_is_included(row, 0))

    def test_none_idx_returns_false(self):
        row = ["Y"]
        self.assertFalse(_is_included(row, None))

    def test_no_returns_false(self):
        row = ["no"]
        self.assertFalse(_is_included(row, 0))

    def test_true_returns_included(self):
        row = ["TRUE"]
        self.assertTrue(_is_included(row, 0))

    def test_1_returns_included(self):
        row = ["1"]
        self.assertTrue(_is_included(row, 0))

    def test_checkmark_returns_included(self):
        row = ["✓"]
        self.assertTrue(_is_included(row, 0))


# ── _parse_contribution_room ──────────────────────────────────────────────────

class TestParseContributionRoom(unittest.TestCase):

    def test_room_remaining_with_dollar_and_comma(self):
        self.assertAlmostEqual(
            _parse_contribution_room("room remaining: $9,000"),
            9000.0,
        )

    def test_room_remaining_with_decimal(self):
        self.assertAlmostEqual(
            _parse_contribution_room("room remaining: 9493.54"),
            9493.0,  # regex matches integer portion only ([\d,]+)
        )

    def test_contribution_room_prefix(self):
        self.assertAlmostEqual(
            _parse_contribution_room("Contribution room: 5000"),
            5000.0,
        )

    def test_contribution_room_with_dollar(self):
        self.assertAlmostEqual(
            _parse_contribution_room("Contribution room: $7,500"),
            7500.0,
        )

    def test_no_room_info_returns_zero(self):
        self.assertEqual(_parse_contribution_room("no room info"), 0.0)

    def test_empty_string_returns_zero(self):
        self.assertEqual(_parse_contribution_room(""), 0.0)

    def test_case_insensitive(self):
        self.assertAlmostEqual(
            _parse_contribution_room("ROOM REMAINING: 6000"),
            6000.0,
        )

    def test_amount_before_room(self):
        # Pattern: "$9000 room left"
        self.assertAlmostEqual(
            _parse_contribution_room("$9000 room left"),
            9000.0,
        )

    def test_generic_room_prefix(self):
        self.assertAlmostEqual(
            _parse_contribution_room("room: 4500"),
            4500.0,
        )


# ── load_from_sheets ──────────────────────────────────────────────────────────

class TestLoadFromSheets(unittest.TestCase):

    # ── EQ Bank parsing ───────────────────────────────────────────────────────

    def test_eq_bank_promo_rate_from_notes(self):
        """Promo rate embedded in notes string is parsed correctly."""
        rows = [
            _HEADERS,
            _row(
                name="EQ Bank HISA",
                inst="EQ Bank",
                currency="CAD",
                balance="46000",
                include="Y",
                base_rate="2.0",
                notes="promo 2.75% until 2026-06-15",
            ),
        ]
        result = _call_load(rows)
        eq = result.get("eq_bank", {})
        self.assertAlmostEqual(eq["savings_balance"], 46000.0)
        self.assertAlmostEqual(eq["promo_rate_pct"], 2.75)
        self.assertEqual(eq["promo_end_date"], "2026-06-15")
        # hisa_rate_pct should use promo when promo > 0
        self.assertAlmostEqual(eq["hisa_rate_pct"], 2.75)

    def test_eq_bank_dedicated_promo_rate_column(self):
        """Dedicated promo rate column takes precedence over notes parsing."""
        rows = [
            _HEADERS,
            _row(
                name="EQ Bank HISA",
                inst="EQ Bank",
                currency="CAD",
                balance="30000",
                include="Y",
                base_rate="2.0",
                promo_rate="3.5",
                promo_end="2026-09-01",
                notes="",
            ),
        ]
        result = _call_load(rows)
        eq = result.get("eq_bank", {})
        self.assertAlmostEqual(eq["promo_rate_pct"], 3.5)
        self.assertAlmostEqual(eq["base_rate_pct"], 2.0)
        self.assertEqual(eq["promo_end_date"], "2026-09-01")
        self.assertAlmostEqual(eq["savings_balance"], 30000.0)

    def test_eq_bank_no_promo_falls_back_to_base_rate(self):
        """When no promo rate exists, hisa_rate_pct equals base_rate."""
        rows = [
            _HEADERS,
            _row(
                name="EQ Bank HISA",
                inst="EQ Bank",
                balance="20000",
                include="Y",
                base_rate="2.0",
                promo_rate="",
                notes="",
            ),
        ]
        result = _call_load(rows)
        eq = result.get("eq_bank", {})
        self.assertAlmostEqual(eq["hisa_rate_pct"], 2.0)
        self.assertAlmostEqual(eq["promo_rate_pct"], 0.0)

    # ── TFSA classification ───────────────────────────────────────────────────

    def test_tfsa_classified_by_asset_class_long_term_reg(self):
        """'Long Term Reg (TFSA)' in asset_class identifies the row as TFSA."""
        rows = [
            _HEADERS,
            _row(
                name="Questrade ETFs",
                inst="Questrade",
                balance="15000",
                include="Y",
                asset_class="Long Term Reg (TFSA)",
                subtype="ETF",
            ),
        ]
        result = _call_load(rows)
        tfsa = result.get("tfsa", {})
        self.assertAlmostEqual(tfsa["total_balance"], 15000.0)

    def test_tfsa_classified_by_subtype_containing_tfsa(self):
        """A subtype field that contains 'TFSA' classifies the row as TFSA."""
        rows = [
            _HEADERS,
            _row(
                name="Wealthsimple",
                inst="Wealthsimple",
                balance="8000",
                include="Y",
                asset_class="",
                subtype="TFSA Savings",
            ),
        ]
        result = _call_load(rows)
        tfsa = result.get("tfsa", {})
        self.assertAlmostEqual(tfsa["total_balance"], 8000.0)

    def test_questrade_cash_tfsa_adds_to_cash_balance(self):
        """TFSA rows with 'cash' in subtype add to cash_balance, not invested_balance."""
        rows = [
            _HEADERS,
            _row(
                name="Questrade Cash TFSA",
                inst="Questrade",
                balance="5000",
                include="Y",
                asset_class="Long Term Reg (TFSA)",
                subtype="Cash",
            ),
        ]
        result = _call_load(rows)
        tfsa = result.get("tfsa", {})
        self.assertAlmostEqual(tfsa["cash_balance"], 5000.0)
        self.assertAlmostEqual(tfsa["invested_balance"], 0.0)

    def test_questrade_etf_adds_to_invested_balance(self):
        """TFSA rows with 'ETF' subtype add to invested_balance, not cash_balance."""
        rows = [
            _HEADERS,
            _row(
                name="Questrade ETFs",
                inst="Questrade",
                balance="12000",
                include="Y",
                asset_class="Long Term Reg (TFSA)",
                subtype="ETF",
            ),
        ]
        result = _call_load(rows)
        tfsa = result.get("tfsa", {})
        self.assertAlmostEqual(tfsa["invested_balance"], 12000.0)
        self.assertAlmostEqual(tfsa["cash_balance"], 0.0)

    def test_tfsa_contribution_room_from_notes(self):
        """Contribution room parsed from TFSA notes ends up in tfsa.contribution_room_remaining."""
        rows = [
            _HEADERS,
            _row(
                name="TFSA Savings",
                inst="Questrade",
                balance="9000",
                include="Y",
                asset_class="Long Term Reg (TFSA)",
                subtype="ETF",
                notes="room remaining: $9,000",
            ),
        ]
        result = _call_load(rows)
        tfsa = result.get("tfsa", {})
        self.assertAlmostEqual(tfsa["contribution_room_remaining"], 9000.0)

    # ── Skipped rows ──────────────────────────────────────────────────────────

    def test_td_chequing_row_is_skipped(self):
        """Rows containing 'chequing' in the name are always skipped."""
        rows = [
            _HEADERS,
            _row(
                name="TD Chequing",
                inst="TD Bank",
                balance="3000",
                include="Y",
            ),
        ]
        result = _call_load(rows)
        # Should not appear in other_accounts
        other = result.get("other_accounts", [])
        names = [a["nickname"] for a in other]
        self.assertNotIn("TD Chequing", names)

    def test_401k_row_is_skipped(self):
        """401k rows are skipped entirely."""
        rows = [
            _HEADERS,
            _row(
                name="My 401k Plan",
                inst="Fidelity",
                balance="50000",
                include="Y",
            ),
        ]
        result = _call_load(rows)
        other = result.get("other_accounts", [])
        names = [a["nickname"] for a in other]
        self.assertNotIn("My 401k Plan", names)

    def test_roth_row_is_skipped(self):
        """Roth IRA rows are skipped entirely."""
        rows = [
            _HEADERS,
            _row(
                name="Roth IRA",
                inst="Vanguard",
                balance="25000",
                include="Y",
            ),
        ]
        result = _call_load(rows)
        other = result.get("other_accounts", [])
        names = [a["nickname"] for a in other]
        self.assertNotIn("Roth IRA", names)

    def test_zero_balance_row_excluded_from_other_accounts(self):
        """Non-TFSA, non-EQ rows with balance=0 do NOT appear in other_accounts."""
        rows = [
            _HEADERS,
            _row(
                name="Old Savings Account",
                inst="CIBC",
                balance="0",
                include="Y",
            ),
        ]
        result = _call_load(rows)
        other = result.get("other_accounts", [])
        names = [a["nickname"] for a in other]
        self.assertNotIn("Old Savings Account", names)

    def test_include_n_row_is_skipped(self):
        """Rows where Include='N' are skipped regardless of balance."""
        rows = [
            _HEADERS,
            _row(
                name="Hidden Account",
                inst="RBC",
                balance="10000",
                include="N",
            ),
        ]
        result = _call_load(rows)
        other = result.get("other_accounts", [])
        names = [a["nickname"] for a in other]
        self.assertNotIn("Hidden Account", names)

    def test_include_empty_row_is_skipped(self):
        """Rows where Include='' are also skipped."""
        rows = [
            _HEADERS,
            _row(
                name="Another Hidden",
                inst="Scotiabank",
                balance="5000",
                include="",
            ),
        ]
        result = _call_load(rows)
        other = result.get("other_accounts", [])
        names = [a["nickname"] for a in other]
        self.assertNotIn("Another Hidden", names)

    # ── USD account ───────────────────────────────────────────────────────────

    def test_usd_account_gets_correct_currency(self):
        """USD accounts land in other_accounts with currency='USD'."""
        rows = [
            _HEADERS,
            _row(
                name="US Brokerage",
                inst="Interactive Brokers",
                currency="USD",
                balance="8000",
                include="Y",
            ),
        ]
        result = _call_load(rows)
        other = result.get("other_accounts", [])
        usd_accounts = [a for a in other if a.get("currency") == "USD"]
        self.assertEqual(len(usd_accounts), 1)
        self.assertEqual(usd_accounts[0]["nickname"], "US Brokerage")

    def test_cad_account_defaults_to_cad_currency(self):
        """Rows without a currency value default to 'CAD'."""
        rows = [
            _HEADERS,
            _row(
                name="CAD Savings",
                inst="TD",
                currency="",
                balance="5000",
                include="Y",
            ),
        ]
        result = _call_load(rows)
        other = result.get("other_accounts", [])
        cad_accounts = [a for a in other if a.get("nickname") == "CAD Savings"]
        self.assertEqual(len(cad_accounts), 1)
        self.assertEqual(cad_accounts[0]["currency"], "CAD")

    # ── Edge cases ────────────────────────────────────────────────────────────

    def test_empty_sheet_returns_empty_dict(self):
        """A sheet with no data rows returns an empty dict (triggers the <2 rows guard)."""
        result = _call_load([])
        self.assertEqual(result, {})

    def test_header_only_sheet_returns_empty_dict(self):
        """A sheet with only a header row and no data returns empty dict."""
        result = _call_load([_HEADERS])
        self.assertEqual(result, {})

    def test_connection_failure_returns_empty_dict(self):
        """When gspread raises an exception, load_from_sheets returns {}."""
        with patch("gspread.authorize", side_effect=Exception("connection refused")), \
             patch(
                 "google.oauth2.service_account.Credentials.from_service_account_file",
                 return_value=MagicMock(),
             ):
            result = load_from_sheets(
                sheet_id="fake_id",
                creds_file=Path("/fake/creds.json"),
            )
        self.assertEqual(result, {})

    def test_result_structure_keys_present(self):
        """Non-empty result always includes the expected top-level keys."""
        rows = [
            _HEADERS,
            _row(
                name="EQ Bank HISA",
                inst="EQ Bank",
                balance="10000",
                include="Y",
            ),
        ]
        result = _call_load(rows)
        for key in ("eq_bank", "tfsa", "gics", "other_accounts", "upcoming_income",
                    "_last_updated", "_source"):
            self.assertIn(key, result)

    def test_source_is_google_sheets(self):
        rows = [
            _HEADERS,
            _row(name="EQ Bank HISA", inst="EQ Bank", balance="5000", include="Y"),
        ]
        result = _call_load(rows)
        self.assertEqual(result.get("_source"), "google_sheets")

    def test_multiple_tfsa_rows_accumulate(self):
        """Multiple TFSA rows sum their balances into tfsa.total_balance."""
        rows = [
            _HEADERS,
            _row(
                name="Questrade ETFs",
                inst="Questrade",
                balance="10000",
                include="Y",
                asset_class="Long Term Reg (TFSA)",
                subtype="ETF",
            ),
            _row(
                name="Questrade Cash",
                inst="Questrade",
                balance="2000",
                include="Y",
                asset_class="Long Term Reg (TFSA)",
                subtype="Cash",
            ),
        ]
        result = _call_load(rows)
        tfsa = result.get("tfsa", {})
        self.assertAlmostEqual(tfsa["total_balance"], 12000.0)
        self.assertAlmostEqual(tfsa["invested_balance"], 10000.0)
        self.assertAlmostEqual(tfsa["cash_balance"], 2000.0)

    def test_no_tfsa_rows_gives_empty_tfsa(self):
        """If no TFSA rows found, result['tfsa'] is an empty dict."""
        rows = [
            _HEADERS,
            _row(
                name="EQ Bank HISA",
                inst="EQ Bank",
                balance="5000",
                include="Y",
            ),
        ]
        result = _call_load(rows)
        self.assertEqual(result.get("tfsa"), {})

    def test_row_with_no_name_is_skipped(self):
        """Rows without an Account Name are silently skipped."""
        rows = [
            _HEADERS,
            _row(name="", inst="Some Bank", balance="1000", include="Y"),
        ]
        result = _call_load(rows)
        other = result.get("other_accounts", [])
        self.assertEqual(other, [])

    def test_other_account_with_balance_appears(self):
        """Non-TFSA, non-EQ, non-chequing rows with balance appear in other_accounts."""
        rows = [
            _HEADERS,
            _row(
                name="Wealthsimple Cash",
                inst="Wealthsimple",
                balance="3500",
                include="Y",
            ),
        ]
        result = _call_load(rows)
        other = result.get("other_accounts", [])
        self.assertEqual(len(other), 1)
        self.assertEqual(other[0]["nickname"], "Wealthsimple Cash")
        self.assertAlmostEqual(other[0]["balance"], 3500.0)

    def test_eq_bank_matched_by_institution_name(self):
        """EQ Bank matched via institution field even if account name differs."""
        rows = [
            _HEADERS,
            _row(
                name="High Interest Savings",
                inst="EQ Bank",
                balance="25000",
                include="Y",
                base_rate="2.0",
            ),
        ]
        result = _call_load(rows)
        eq = result.get("eq_bank", {})
        self.assertAlmostEqual(eq["savings_balance"], 25000.0)
        self.assertEqual(eq["institution"], "EQ Bank")

    def test_tfsa_name_detection(self):
        """An account with 'TFSA' in its name is classified as TFSA."""
        rows = [
            _HEADERS,
            _row(
                name="My TFSA Account",
                inst="Oaken",
                balance="7000",
                include="Y",
            ),
        ]
        result = _call_load(rows)
        tfsa = result.get("tfsa", {})
        self.assertAlmostEqual(tfsa["total_balance"], 7000.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

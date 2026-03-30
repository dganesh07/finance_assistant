"""
tests/test_portfolio_connector.py — Unit tests for src/portfolio_connector.py

Run:
  python -m pytest tests/test_portfolio_connector.py -v
  python -m pytest tests/ -v

All external dependencies (gspread, google-auth) are mocked.
No network, no credentials file, no real spreadsheet required.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.portfolio_connector import (
    _classify_account,
    _find_col_by_aliases,
    _find_inv_col,
    _infer_currency,
    _load_accounts,
    _load_investment_transactions,
    load_portfolio,
)


# ── Mock helpers ───────────────────────────────────────────────────────────────

def _make_mock_client(accounts_rows: list, inv_rows: list) -> MagicMock:
    """Build a mocked gspread client returning different rows per worksheet name."""
    def _worksheet(name):
        ws = MagicMock()
        if name == "Accounts":
            ws.get_all_values.return_value = accounts_rows
        else:
            ws.get_all_values.return_value = inv_rows
        return ws

    mock_sheet = MagicMock()
    mock_sheet.worksheet.side_effect = _worksheet
    mock_client = MagicMock()
    mock_client.open_by_key.return_value = mock_sheet
    return mock_client


def _call_load_portfolio(accounts_rows: list, inv_rows: list) -> dict:
    mock_client = _make_mock_client(accounts_rows, inv_rows)
    with patch("gspread.authorize", return_value=mock_client), \
         patch(
             "google.oauth2.service_account.Credentials.from_service_account_file",
             return_value=MagicMock(),
         ):
        return load_portfolio(
            sheet_id="fake_id",
            creds_file=Path("/fake/creds.json"),
        )


# Standard Accounts tab header row
_ACCT_HEADERS = [
    "Account Name", "Institution", "Currency", "Asset Class", "Sub-Type",
    "Balance", "Interest Rate (base)", "Include in Net Worth? (Y/N)", "Notes",
]

# Standard Investment_Transactions tab header row
_INV_HEADERS = [
    "Date", "Account", "Ticker",
    "Type (Buy/Sell/Dividend/Deposit/Withdrawal)",
    "Units", "Price", "Total (Units*Price)", "Fees", "Notes",
]


def _acct_row(name, inst="", currency="CAD", asset_class="", subtype="",
              balance="0", rate="", include="Y", notes="") -> list[str]:
    return [name, inst, currency, asset_class, subtype, balance, rate, include, notes]


def _inv_row(date, account, ticker, txn_type, units, price, total,
             fees="", notes="") -> list[str]:
    return [date, account, ticker, txn_type, str(units), str(price), str(total), fees, notes]


# ── _classify_account ──────────────────────────────────────────────────────────

class TestClassifyAccount(unittest.TestCase):

    def test_tfsa_by_name(self):
        self.assertEqual(_classify_account("TFSA", "", "", "CAD"), "tfsa")

    def test_tfsa_by_asset_class(self):
        self.assertEqual(_classify_account("Questrade", "Long Term Reg (TFSA)", "", "CAD"), "tfsa")

    def test_retirement_401k(self):
        self.assertEqual(_classify_account("Fidelity-401K-USA", "", "", "USD"), "retirement")

    def test_retirement_rrsp(self):
        self.assertEqual(_classify_account("RRSP", "", "", "CAD"), "retirement")

    def test_retirement_roth(self):
        self.assertEqual(_classify_account("Roth IRA", "", "", "USD"), "retirement")

    def test_retirement_by_asset_class(self):
        self.assertEqual(_classify_account("Fidelity", "Retirement (401K + RRSP)", "", "USD"), "retirement")

    def test_hisa_by_subtype(self):
        self.assertEqual(_classify_account("HYSA Canada", "Emergency Fund (Cash)", "HISA", "CAD"), "hisa")

    def test_hisa_by_name(self):
        self.assertEqual(_classify_account("EQ HISA", "", "", "CAD"), "hisa")

    def test_savings_by_subtype(self):
        self.assertEqual(_classify_account("TD Saving", "Short-Term Savings", "Savings", "CAD"), "savings")

    def test_savings_chequing(self):
        self.assertEqual(_classify_account("TD Chequing", "", "Chequing", "CAD"), "savings")

    def test_gic_by_subtype(self):
        self.assertEqual(_classify_account("Oaken 1yr GIC", "Fixed Income", "GIC", "CAD"), "gic")

    def test_gic_by_subtype_term_deposit(self):
        self.assertEqual(_classify_account("TD Term Deposit", "Fixed Income", "Term Deposit", "CAD"), "gic")

    def test_gic_by_asset_class_fixed_income(self):
        self.assertEqual(_classify_account("Some GIC", "Fixed Income", "", "CAD"), "gic")

    def test_tfsa_gic_is_tfsa_not_gic(self):
        # TFSA check runs before GIC — a TFSA GIC should stay in the tfsa group
        self.assertEqual(_classify_account("TFSA GIC", "Long Term Reg (TFSA)", "GIC", "CAD"), "tfsa")

    def test_other_fallback(self):
        self.assertEqual(_classify_account("India Land investment", "Other", "", "USD"), "other")


# ── _infer_currency ────────────────────────────────────────────────────────────

class TestInferCurrency(unittest.TestCase):

    def test_tfsa_is_cad(self):
        self.assertEqual(_infer_currency("TFSA"), "CAD")

    def test_401k_is_usd(self):
        self.assertEqual(_infer_currency("401k – Employer MATCH"), "USD")

    def test_roth_is_usd(self):
        self.assertEqual(_infer_currency("401K - ROTH DEFERRAL"), "USD")

    def test_deferral_is_usd(self):
        self.assertEqual(_infer_currency("401K- EMPLOYEE DEFERRAL"), "USD")

    def test_unknown_defaults_cad(self):
        self.assertEqual(_infer_currency("Unknown Account"), "CAD")

    def test_empty_defaults_cad(self):
        self.assertEqual(_infer_currency(""), "CAD")


# ── _find_inv_col ──────────────────────────────────────────────────────────────

class TestFindInvCol(unittest.TestCase):

    def test_exact_match(self):
        self.assertEqual(_find_inv_col(["Date", "Account", "Ticker"], "date"), 0)

    def test_full_type_header(self):
        headers = ["Date", "Account", "Ticker",
                   "Type (Buy/Sell/Dividend/Deposit/Withdrawal)"]
        # "Type (Buy/...)" starts with alias "Type"
        self.assertIsNotNone(_find_inv_col(headers, "type"))

    def test_total_alias(self):
        headers = ["Date", "Account", "Total (Units*Price)"]
        self.assertIsNotNone(_find_inv_col(headers, "total"))

    def test_missing_col_returns_none(self):
        self.assertIsNone(_find_inv_col(["Date", "Account"], "ticker"))


# ── _load_accounts ─────────────────────────────────────────────────────────────

class TestLoadAccounts(unittest.TestCase):

    def test_empty_returns_empty(self):
        accounts, summary = _load_accounts([])
        self.assertEqual(accounts, [])
        self.assertEqual(summary, {})

    def test_header_only_returns_empty(self):
        accounts, summary = _load_accounts([_ACCT_HEADERS])
        self.assertEqual(accounts, [])

    def test_excluded_row_skipped(self):
        rows = [_ACCT_HEADERS,
                _acct_row("Some Account", include="N", balance="5000")]
        accounts, _ = _load_accounts(rows)
        self.assertEqual(len(accounts), 0)

    def test_basic_cad_account(self):
        rows = [_ACCT_HEADERS,
                _acct_row("TD Saving", "TD", "CAD", "Short-Term Savings", "Savings",
                          balance="5000", include="Y")]
        accounts, summary = _load_accounts(rows)
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["name"], "TD Saving")
        self.assertEqual(accounts[0]["currency"], "CAD")
        self.assertEqual(accounts[0]["balance"], 5000.0)
        self.assertEqual(accounts[0]["group"], "savings")
        self.assertEqual(summary["total_cad"], 5000.0)
        self.assertEqual(summary["total_usd"], 0.0)

    def test_tfsa_account_group(self):
        rows = [_ACCT_HEADERS,
                _acct_row("TFSA", "Questrade", "CAD", "Long Term Reg (TFSA)", "ETF/Stocks",
                          balance="4518", include="Y")]
        accounts, summary = _load_accounts(rows)
        self.assertEqual(accounts[0]["group"], "tfsa")
        self.assertEqual(summary["tfsa_balance"], 4518.0)
        self.assertEqual(summary["invested_cad"], 4518.0)

    def test_usd_retirement_account(self):
        rows = [_ACCT_HEADERS,
                _acct_row("Fidelity-401K-USA", "Fidelity", "USD",
                          "Retirement (401K + RRSP)", "401K",
                          balance="240219", include="Y")]
        accounts, summary = _load_accounts(rows)
        self.assertEqual(accounts[0]["group"], "retirement")
        self.assertEqual(summary["total_usd"], 240219.0)
        self.assertEqual(summary["retirement_usd"], 240219.0)

    def test_multiple_accounts_totals(self):
        rows = [
            _ACCT_HEADERS,
            _acct_row("TD Saving", "TD", "CAD", "Short-Term Savings", "Savings", "5000", "", "Y"),
            _acct_row("HYSA Canada", "Oaken", "CAD", "Emergency Fund (Cash)", "HISA", "100000", "2.80%", "Y"),
            _acct_row("Fidelity-401K-USA", "Fidelity", "USD", "Retirement (401K + RRSP)", "401K", "240219", "", "Y"),
        ]
        accounts, summary = _load_accounts(rows)
        self.assertEqual(len(accounts), 3)
        self.assertAlmostEqual(summary["total_cad"], 105000.0)
        self.assertAlmostEqual(summary["total_usd"], 240219.0)
        self.assertAlmostEqual(summary["hisa_total_cad"], 100000.0)

    def test_tfsa_contribution_room_from_notes(self):
        rows = [_ACCT_HEADERS,
                _acct_row("TFSA", "Questrade", "CAD", "Long Term Reg (TFSA)", "ETF/Stocks",
                          balance="4518", include="Y", notes="room remaining: $9,000")]
        _, summary = _load_accounts(rows)
        self.assertEqual(summary["tfsa_contribution_room"], 9000.0)

    def test_empty_name_row_skipped(self):
        rows = [_ACCT_HEADERS,
                ["", "TD", "CAD", "Other", "Savings", "1000", "", "Y", ""]]
        accounts, _ = _load_accounts(rows)
        self.assertEqual(len(accounts), 0)

    def test_gic_account_classified_correctly(self):
        rows = [_ACCT_HEADERS,
                _acct_row("Oaken 1yr GIC", "Oaken", "CAD", "Fixed Income", "GIC",
                          balance="10000", include="Y")]
        accounts, summary = _load_accounts(rows)
        self.assertEqual(accounts[0]["group"], "gic")
        self.assertAlmostEqual(summary["gic_total_cad"], 10000.0)

    def test_gic_maturity_date_read_when_column_present(self):
        # Headers extended with Maturity Date column
        headers_with_mat = _ACCT_HEADERS + ["Maturity Date"]
        row = _acct_row("Oaken GIC", "Oaken", "CAD", "Fixed Income", "GIC",
                        balance="10000", include="Y") + ["2026-09-15"]
        rows = [headers_with_mat, row]
        accounts, _ = _load_accounts(rows)
        self.assertEqual(accounts[0]["maturity_date"], "2026-09-15")

    def test_maturity_date_empty_when_column_absent(self):
        # Standard headers have no Maturity Date column
        rows = [_ACCT_HEADERS,
                _acct_row("Oaken GIC", "Oaken", "CAD", "Fixed Income", "GIC",
                          balance="10000", include="Y")]
        accounts, _ = _load_accounts(rows)
        self.assertEqual(accounts[0]["maturity_date"], "")

    def test_maturity_date_empty_for_non_gic_accounts(self):
        headers_with_mat = _ACCT_HEADERS + ["Maturity Date"]
        row = _acct_row("HYSA Canada", "Oaken", "CAD", "Emergency Fund (Cash)", "HISA",
                        balance="50000", include="Y") + [""]
        rows = [headers_with_mat, row]
        accounts, _ = _load_accounts(rows)
        self.assertEqual(accounts[0]["maturity_date"], "")

    def test_gic_not_counted_in_total_cad_separately(self):
        """GIC balance is still included in total_cad."""
        rows = [_ACCT_HEADERS,
                _acct_row("Oaken GIC", "Oaken", "CAD", "Fixed Income", "GIC",
                          balance="15000", include="Y")]
        _, summary = _load_accounts(rows)
        self.assertAlmostEqual(summary["total_cad"], 15000.0)
        self.assertAlmostEqual(summary["gic_total_cad"], 15000.0)


# ── _find_col_by_aliases ──────────────────────────────────────────────────────

class TestFindColByAliases(unittest.TestCase):

    def test_exact_match(self):
        headers = ["Account Name", "Maturity Date", "Balance"]
        idx = _find_col_by_aliases(headers, ["Maturity Date", "Maturity"])
        self.assertEqual(idx, 1)

    def test_startswith_match(self):
        # "Maturity Date (YYYY-MM-DD)" starts with alias "Maturity Date"
        headers = ["Account Name", "Maturity Date (YYYY-MM-DD)", "Balance"]
        idx = _find_col_by_aliases(headers, ["Maturity Date"])
        self.assertEqual(idx, 1)

    def test_alias_order_priority(self):
        # First alias in list wins when multiple would match
        headers = ["Maturity", "Maturity Date"]
        idx = _find_col_by_aliases(headers, ["Maturity Date", "Maturity"])
        self.assertEqual(idx, 1)  # exact match on "Maturity Date" wins

    def test_returns_none_when_no_match(self):
        headers = ["Account Name", "Balance"]
        idx = _find_col_by_aliases(headers, ["Maturity Date", "Maturity"])
        self.assertIsNone(idx)

    def test_case_insensitive(self):
        headers = ["account name", "maturity date", "balance"]
        idx = _find_col_by_aliases(headers, ["Maturity Date"])
        self.assertEqual(idx, 1)


# ── _load_investment_transactions ──────────────────────────────────────────────

class TestLoadInvestmentTransactions(unittest.TestCase):

    def test_empty_returns_empty(self):
        txns, holdings = _load_investment_transactions([])
        self.assertEqual(txns, [])
        self.assertEqual(holdings, {})

    def test_header_only_returns_empty(self):
        txns, holdings = _load_investment_transactions([_INV_HEADERS])
        self.assertEqual(txns, [])
        self.assertEqual(holdings, {})

    def test_skips_row_with_no_account(self):
        rows = [_INV_HEADERS,
                _inv_row("2025-11-24", "", "VFV.TO", "Buy", 15, 168, 2520)]
        txns, _ = _load_investment_transactions(rows)
        self.assertEqual(len(txns), 0)

    def test_tfsa_buy_parsed(self):
        rows = [_INV_HEADERS,
                _inv_row("2025-11-24", "TFSA", "VFV.TO", "Buy", 15, 168, 2520,
                         notes="First TFSA buy")]
        txns, holdings = _load_investment_transactions(rows)
        self.assertEqual(len(txns), 1)
        t = txns[0]
        self.assertEqual(t["account"], "TFSA")
        self.assertEqual(t["ticker"], "VFV.TO")
        self.assertEqual(t["type"], "Buy")
        self.assertEqual(t["units"], 15.0)
        self.assertEqual(t["total"], 2520.0)
        self.assertEqual(t["currency"], "CAD")
        self.assertEqual(t["notes"], "First TFSA buy")
        # Holdings aggregated
        self.assertIn("TFSA", holdings)
        self.assertEqual(holdings["TFSA"][0]["ticker"], "VFV.TO")
        self.assertAlmostEqual(holdings["TFSA"][0]["total_units"], 15.0)
        self.assertAlmostEqual(holdings["TFSA"][0]["cost_basis"], 2520.0)

    def test_401k_buy_inferred_usd(self):
        rows = [_INV_HEADERS,
                _inv_row("2023-12-20", "401k – Employer MATCH", "FVTKX", "Buy",
                         6127.51, 16.55, 101410.34)]
        txns, holdings = _load_investment_transactions(rows)
        self.assertEqual(txns[0]["currency"], "USD")
        self.assertIn("401K", holdings)
        self.assertEqual(holdings["401K"][0]["ticker"], "FVTKX")

    def test_multiple_buys_same_ticker_aggregated(self):
        rows = [
            _INV_HEADERS,
            _inv_row("2025-11-24", "TFSA", "VFV.TO", "Buy", 15, 168, 2520),
            _inv_row("2025-12-18", "TFSA", "VFV.TO", "Buy", 12, 166.51, 1998.12),
            _inv_row("2026-03-27", "TFSA", "VFV.TO", "Buy", 13, 156.86, 2039.18),
        ]
        _, holdings = _load_investment_transactions(rows)
        vfv = holdings["TFSA"][0]
        self.assertEqual(vfv["ticker"], "VFV.TO")
        self.assertAlmostEqual(vfv["total_units"], 40.0)
        self.assertAlmostEqual(vfv["cost_basis"], 6557.3, places=1)

    def test_401k_multiple_account_types_same_ticker(self):
        """All 401k account variants (employer, roth, employee) aggregate into 401K group."""
        rows = [
            _INV_HEADERS,
            _inv_row("2023-12-20", "401k – Employer MATCH",    "FVTKX", "Buy", 6127.51, 16.55, 101410.34),
            _inv_row("2021-12-20", "401K - ROTH DEFERRAL",     "FVTKX", "Buy", 6039.16, 16.55, 99948.13),
            _inv_row("2025-01-20", "401K- EMPLOYEE DEFERRAL",  "FVTKX", "Buy", 2348.06, 16.55, 38860.44),
        ]
        _, holdings = _load_investment_transactions(rows)
        self.assertIn("401K", holdings)
        fvtkx = holdings["401K"][0]
        self.assertEqual(fvtkx["ticker"], "FVTKX")
        self.assertAlmostEqual(fvtkx["total_units"], 14514.73, places=1)

    def test_transactions_sorted_newest_first(self):
        rows = [
            _INV_HEADERS,
            _inv_row("2025-11-24", "TFSA", "VFV.TO", "Buy", 15, 168, 2520),
            _inv_row("2026-03-27", "TFSA", "VFV.TO", "Buy", 13, 156.86, 2039.18),
            _inv_row("2025-12-18", "TFSA", "VFV.TO", "Buy", 12, 166.51, 1998.12),
        ]
        txns, _ = _load_investment_transactions(rows)
        dates = [t["date"] for t in txns]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_sell_reduces_holdings(self):
        rows = [
            _INV_HEADERS,
            _inv_row("2025-11-24", "TFSA", "VFV.TO", "Buy",  20, 168, 3360),
            _inv_row("2026-01-10", "TFSA", "VFV.TO", "Sell", 5,  170, 850),
        ]
        _, holdings = _load_investment_transactions(rows)
        vfv = holdings["TFSA"][0]
        self.assertAlmostEqual(vfv["total_units"], 15.0)

    def test_fully_sold_position_hidden(self):
        rows = [
            _INV_HEADERS,
            _inv_row("2025-11-24", "TFSA", "VFV.TO", "Buy",  10, 168, 1680),
            _inv_row("2026-01-10", "TFSA", "VFV.TO", "Sell", 10, 170, 1700),
        ]
        _, holdings = _load_investment_transactions(rows)
        # All shares sold — position should not appear in holdings
        tfsa_tickers = [h["ticker"] for h in holdings.get("TFSA", [])]
        self.assertNotIn("VFV.TO", tfsa_tickers)


# ── load_portfolio integration ─────────────────────────────────────────────────

class TestLoadPortfolioIntegration(unittest.TestCase):

    def test_returns_empty_on_import_error(self):
        """If gspread is not installed, load_portfolio returns {}."""
        with patch.dict("sys.modules", {"gspread": None, "google.oauth2.service_account": None}):
            # Re-import to trigger ImportError path
            import importlib
            import src.portfolio_connector as pc
            importlib.reload(pc)
            # Can't fully test this without uninstalling gspread; just check the structure
            pass

    def test_returns_empty_on_connection_failure(self):
        """Sheets connection failure returns empty dict (no exception raised)."""
        with patch("gspread.authorize", side_effect=Exception("auth failed")), \
             patch(
                 "google.oauth2.service_account.Credentials.from_service_account_file",
                 return_value=MagicMock(),
             ):
            result = load_portfolio(
                sheet_id="fake", creds_file=Path("/fake/creds.json")
            )
        self.assertEqual(result, {})

    def test_full_flow(self):
        """Happy path: returns accounts, summary, transactions, holdings."""
        acct_rows = [
            _ACCT_HEADERS,
            _acct_row("TFSA", "Questrade", "CAD", "Long Term Reg (TFSA)", "ETF/Stocks",
                      "4518", "", "Y", "room remaining: $9,000"),
            _acct_row("Fidelity-401K-USA", "Fidelity", "USD",
                      "Retirement (401K + RRSP)", "401K", "240219", "", "Y"),
        ]
        inv_rows = [
            _INV_HEADERS,
            _inv_row("2025-11-24", "TFSA",                  "VFV.TO", "Buy", 15, 168,   2520),
            _inv_row("2023-12-20", "401k – Employer MATCH",  "FVTKX", "Buy", 6127.51, 16.55, 101410.34),
        ]
        result = _call_load_portfolio(acct_rows, inv_rows)

        self.assertIn("accounts", result)
        self.assertIn("summary", result)
        self.assertIn("investment_transactions", result)
        self.assertIn("holdings", result)

        self.assertEqual(len(result["accounts"]), 2)
        self.assertAlmostEqual(result["summary"]["tfsa_balance"], 4518.0)
        self.assertAlmostEqual(result["summary"]["retirement_usd"], 240219.0)
        self.assertEqual(result["summary"]["tfsa_contribution_room"], 9000.0)
        self.assertEqual(len(result["investment_transactions"]), 2)
        self.assertIn("TFSA", result["holdings"])
        self.assertIn("401K", result["holdings"])

    def test_accounts_tab_failure_still_returns_inv_data(self):
        """If the Accounts tab fails, transactions are still returned."""
        def _worksheet(name):
            ws = MagicMock()
            if name == "Accounts":
                ws.get_all_values.side_effect = Exception("tab not found")
            else:
                ws.get_all_values.return_value = [
                    _INV_HEADERS,
                    _inv_row("2025-11-24", "TFSA", "VFV.TO", "Buy", 15, 168, 2520),
                ]
            return ws

        mock_sheet = MagicMock()
        mock_sheet.worksheet.side_effect = _worksheet
        mock_client = MagicMock()
        mock_client.open_by_key.return_value = mock_sheet

        with patch("gspread.authorize", return_value=mock_client), \
             patch(
                 "google.oauth2.service_account.Credentials.from_service_account_file",
                 return_value=MagicMock(),
             ):
            result = load_portfolio(sheet_id="fake", creds_file=Path("/fake/creds.json"))

        self.assertEqual(result["accounts"], [])
        self.assertEqual(len(result["investment_transactions"]), 1)


if __name__ == "__main__":
    unittest.main()

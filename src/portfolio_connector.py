"""
src/portfolio_connector.py — Reads portfolio data from Google Sheets.

Separate from the expense flow (sheets_connector.py / context_builder.py).
No DB writes — this is a read-only view of your Google Sheet.

Reads two tabs:
  1. Accounts             — all account balances, currencies, asset classes
  2. Investment_Transactions — TFSA and 401K transaction history

Returns a structured dict consumed by GET /api/portfolio.

Expected column layout:

  Accounts tab:
    Account Name | Institution | Currency | Asset Class | Sub-Type |
    Balance | Interest Rate (base) | Include in Net Worth? (Y/N) | Notes

  Investment_Transactions tab:
    Date | Account | Ticker | Type | Units | Price | Total (Units*Price) | Fees | Notes

Column names are matched flexibly (case-insensitive, alias list) — if your
sheet uses slightly different headers the alias lists below can be extended.

Call load_portfolio() to get the combined dict.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

# Reuse shared helpers from sheets_connector (column matching, cell parsing)
from src.sheets_connector import (
    _COL_ALIASES,
    _cell,
    _find_col,
    _is_included,
    _num,
    _parse_contribution_room,
)

logger = logging.getLogger(__name__)


# ── Investment_Transactions column aliases ────────────────────────────────────
# Extend these lists if your sheet uses different header names.

_INV_COL_ALIASES: list[tuple[str, list[str]]] = [
    ("date",    ["Date"]),
    ("account", ["Account", "Account Name"]),
    ("ticker",  ["Ticker", "Symbol", "Ticker/Symbol"]),
    ("type",    ["Type", "Type (Buy/Sell/Dividend/Deposit/Withdrawal)", "Transaction Type"]),
    ("units",   ["Units", "Shares", "Quantity"]),
    ("price",   ["Price", "Price per Unit", "Unit Price"]),
    ("total",   ["Total", "Total (Units*Price)", "Amount", "Value"]),
    ("fees",    ["Fees", "Fee", "Commission"]),
    ("notes",   ["Notes", "Note", "Comments"]),
]


def _find_inv_col(headers: list[str], key: str) -> int | None:
    """Find a column index in the Investment_Transactions tab by key."""
    aliases = next((a for k, a in _INV_COL_ALIASES if k == key), [])
    clean = [h.strip().lower() for h in headers]
    for alias in aliases:
        a = alias.lower()
        if a in clean:
            return clean.index(a)
    for alias in aliases:
        a = alias.lower()
        for i, h in enumerate(clean):
            if h.startswith(a) or (h and a.startswith(h)):
                return i
    return None


# ── Account grouping logic ────────────────────────────────────────────────────

def _classify_account(name: str, asset_class: str, subtype: str, currency: str) -> str:
    """
    Assign each account row to a logical group for display.

    Groups:
      tfsa        — TFSA accounts (CAD registered)
      retirement  — 401k, RRSP, pension
      gic         — GICs / term deposits (Sub-Type = "GIC" or "Term Deposit")
      hisa        — High-interest savings / emergency fund cash
      savings     — Short-term savings / chequing
      other       — Everything else included in net worth

    Priority order matters: a TFSA GIC should classify as "tfsa" not "gic",
    so TFSA check runs first.
    """
    n, a, s = name.lower(), asset_class.lower(), subtype.lower()

    # TFSA first — a TFSA GIC is still a TFSA for grouping purposes
    if "tfsa" in n or "tfsa" in a or "tfsa" in s or "long term reg" in a:
        return "tfsa"

    if any(x in n for x in ("401k", "401(k)", "roth", "rrsp", "pension")):
        return "retirement"
    if "retirement" in a or "401k" in s or "rrsp" in s:
        return "retirement"

    # GIC / term deposit — matched by Sub-Type column
    if any(x in s for x in ("gic", "term deposit", "term")):
        return "gic"
    if any(x in a for x in ("gic", "fixed income", "term deposit")):
        return "gic"

    if "hisa" in s or "hisa" in n or "emergency fund" in a:
        return "hisa"

    if any(x in s for x in ("savings", "chequing", "checking")):
        return "savings"
    if "short-term" in a or "short term" in a:
        return "savings"

    return "other"


def _find_col_by_aliases(headers: list[str], aliases: list[str]) -> int | None:
    """
    Flexible 3-pass column lookup against an explicit alias list.
    Used for portfolio-specific columns not in sheets_connector._COL_ALIASES
    (e.g. Maturity Date — only relevant to the portfolio view).
    """
    clean = [h.strip().lower() for h in headers]
    for alias in aliases:
        a = alias.lower()
        if a in clean:
            return clean.index(a)
    for alias in aliases:
        a = alias.lower()
        for i, h in enumerate(clean):
            if h.startswith(a) or (h and a.startswith(h)):
                return i
    return None


# ── Currency inference for investment transactions ────────────────────────────

def _infer_currency(account: str) -> str:
    """
    Infer transaction currency from the account name.

    - TFSA → CAD
    - 401k, ROTH, 403b, DEFERRAL → USD
    - Default → CAD (most Canadian accounts are CAD)
    """
    a = account.lower()
    if any(x in a for x in ("401k", "401(k)", "roth", "deferral", "403b")):
        return "USD"
    return "CAD"


# ── Accounts tab reader ───────────────────────────────────────────────────────

def _load_accounts(ws_rows: list[list[str]]) -> tuple[list[dict], dict]:
    """
    Parse the Accounts tab rows into a list of account dicts and a summary dict.

    Returns (accounts, summary) where:
      accounts — one dict per row with all fields
      summary  — aggregated totals (CAD / USD split, group balances)
    """
    if len(ws_rows) < 2:
        return [], {}

    headers   = ws_rows[0]
    data_rows = ws_rows[1:]
    cols      = {key: _find_col(headers, key) for key, _ in _COL_ALIASES}

    # Maturity Date is a portfolio-only column — not in sheets_connector._COL_ALIASES
    maturity_col = _find_col_by_aliases(
        headers,
        ["Maturity Date", "Maturity", "Term End Date", "Term End", "Matures", "Expiry Date"],
    )

    accounts: list[dict] = []

    # Contribution room is extracted from TFSA notes
    tfsa_contribution_room = 0.0

    for row in data_rows:
        name          = _cell(row, cols["name"])
        institution   = _cell(row, cols["inst"])
        currency      = _cell(row, cols["currency"]) or "CAD"
        asset_class   = _cell(row, cols["asset_class"])
        subtype       = _cell(row, cols["subtype"])
        balance       = _num(row,  cols["balance"])
        base_rate     = _num(row,  cols["base_rate"])
        notes         = _cell(row, cols["notes"])
        maturity_date = _cell(row, maturity_col)   # empty string if column absent or cell blank

        if not name:
            continue
        if not _is_included(row, cols["include"]):
            continue

        group = _classify_account(name, asset_class, subtype, currency)

        # Extract TFSA contribution room from notes field
        if group == "tfsa" and notes:
            room = _parse_contribution_room(notes)
            if room > 0:
                tfsa_contribution_room = room

        accounts.append({
            "name":          name,
            "institution":   institution,
            "currency":      currency,
            "asset_class":   asset_class,
            "subtype":       subtype,
            "balance":       balance,
            "base_rate":     base_rate,
            "maturity_date": maturity_date,  # populated for GICs; empty for everything else
            "group":         group,          # tfsa | retirement | gic | hisa | savings | other
            "notes":         notes,
        })

    # ── Summary calculations ─────────────────────────────────────────────────
    summary: dict[str, Any] = {
        "total_cad":          0.0,
        "total_usd":          0.0,
        "tfsa_balance":       0.0,
        "retirement_usd":     0.0,
        "gic_total_cad":      0.0,   # sum of all non-TFSA GICs in CAD
        "hisa_total_cad":     0.0,
        "savings_total_cad":  0.0,
        "invested_cad":       0.0,   # TFSA + CAD retirement (RRSP)
        "tfsa_contribution_room": tfsa_contribution_room,
    }

    for acct in accounts:
        bal = acct["balance"]
        cur = acct["currency"]
        grp = acct["group"]

        if cur == "CAD":
            summary["total_cad"] += bal
        else:
            summary["total_usd"] += bal

        if grp == "tfsa":
            summary["tfsa_balance"]   += bal
            summary["invested_cad"]   += bal
        elif grp == "retirement":
            if cur == "USD":
                summary["retirement_usd"] += bal
            else:
                summary["invested_cad"] += bal
        elif grp == "gic":
            if cur == "CAD":
                summary["gic_total_cad"] += bal
        elif grp == "hisa":
            if cur == "CAD":
                summary["hisa_total_cad"] += bal
        elif grp == "savings":
            if cur == "CAD":
                summary["savings_total_cad"] += bal

    return accounts, summary


# ── Investment_Transactions tab reader ────────────────────────────────────────

def _load_investment_transactions(ws_rows: list[list[str]]) -> tuple[list[dict], dict]:
    """
    Parse the Investment_Transactions tab into a list of transaction dicts
    and an aggregated holdings dict (total units + cost basis per ticker per account group).

    Returns (transactions, holdings) where:
      transactions — one dict per row, newest first (sorted by date desc)
      holdings     — {"TFSA": [{"ticker", "total_units", "cost_basis", "currency"}], "401K": [...]}
    """
    if len(ws_rows) < 2:
        return [], {}

    headers   = ws_rows[0]
    data_rows = ws_rows[1:]
    cols      = {key: _find_inv_col(headers, key) for key, _ in _INV_COL_ALIASES}

    transactions: list[dict] = []

    for row in data_rows:
        date_str = _cell(row, cols["date"])
        account  = _cell(row, cols["account"])
        ticker   = _cell(row, cols["ticker"])
        txn_type = _cell(row, cols["type"])
        units    = _num(row,  cols["units"])
        price    = _num(row,  cols["price"])
        total    = _num(row,  cols["total"])
        fees     = _num(row,  cols["fees"])
        notes    = _cell(row, cols["notes"])

        if not account or not date_str:
            continue

        currency = _infer_currency(account)

        transactions.append({
            "date":     date_str,
            "account":  account,
            "ticker":   ticker,
            "type":     txn_type,
            "units":    units,
            "price":    price,
            "total":    total,
            "fees":     fees,
            "currency": currency,
            "notes":    notes,
        })

    # Sort newest first for display
    transactions.sort(key=lambda t: t["date"], reverse=True)

    # ── Aggregate holdings per group ─────────────────────────────────────────
    # Group account names: anything with 401 → "401K", "TFSA" → "TFSA"
    # Aggregate total units and cost basis per (group, ticker)

    # {group: {ticker: {"total_units": float, "cost_basis": float, "currency": str}}}
    _agg: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(
        lambda: {"total_units": 0.0, "cost_basis": 0.0, "currency": "CAD"}
    ))

    for txn in transactions:
        account_l = txn["account"].lower()
        ticker    = txn["ticker"]
        if not ticker:
            continue

        # Route to holding group
        if "401" in account_l or "roth" in account_l or "deferral" in account_l:
            group = "401K"
        elif "tfsa" in account_l:
            group = "TFSA"
        else:
            group = "Other"

        t = txn["type"].lower() if txn["type"] else ""
        if t in ("buy", "deposit", "contribution"):
            _agg[group][ticker]["total_units"] += txn["units"]
            _agg[group][ticker]["cost_basis"]  += txn["total"]
        elif t in ("sell", "withdrawal"):
            _agg[group][ticker]["total_units"] -= txn["units"]
            _agg[group][ticker]["cost_basis"]  -= txn["total"]

        _agg[group][ticker]["currency"] = txn["currency"]

    # Convert to serializable list format
    holdings: dict[str, list[dict]] = {}
    for group, tickers in _agg.items():
        holdings[group] = [
            {
                "ticker":      ticker,
                "total_units": round(data["total_units"], 4),
                "cost_basis":  round(data["cost_basis"], 2),
                "currency":    data["currency"],
            }
            for ticker, data in sorted(tickers.items())
            if data["total_units"] > 0  # hide fully sold positions
        ]

    return transactions, holdings


# ── Main public function ──────────────────────────────────────────────────────

def load_portfolio(
    sheet_id:   str,
    creds_file: Path,
    accounts_tab:     str = "Accounts",
    investments_tab:  str = "Investment_Transactions",
) -> dict[str, Any]:
    """
    Read both Google Sheets tabs and return a combined portfolio dict.

    Returns empty dict on any connection failure so the caller handles it
    gracefully — errors are logged, not raised.

    Dict shape:
      {
        "_last_updated": "YYYY-MM-DD",
        "_source":       "google_sheets",
        "accounts":      [...],          # one dict per account row
        "summary":       {...},          # aggregated totals
        "investment_transactions": [...],# one dict per transaction row
        "holdings":      {...},          # TFSA / 401K ticker aggregates
      }
    """
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        logger.warning(
            "gspread / google-auth not installed. "
            "Run: pip install gspread google-auth"
        )
        return {}

    try:
        creds  = Credentials.from_service_account_file(
            str(creds_file),
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
        )
        client = gspread.authorize(creds)
        sheet  = client.open_by_key(sheet_id)
    except Exception as exc:
        logger.warning("Google Sheets connection failed: %s", exc)
        return {}

    # ── Accounts tab ─────────────────────────────────────────────────────────
    try:
        accounts_rows = sheet.worksheet(accounts_tab).get_all_values()
    except Exception as exc:
        logger.warning("Failed to read Accounts tab '%s': %s", accounts_tab, exc)
        accounts_rows = []

    accounts, summary = _load_accounts(accounts_rows)

    # ── Investment_Transactions tab ───────────────────────────────────────────
    try:
        inv_rows = sheet.worksheet(investments_tab).get_all_values()
    except Exception as exc:
        logger.warning(
            "Failed to read Investment_Transactions tab '%s': %s",
            investments_tab, exc,
        )
        inv_rows = []

    transactions, holdings = _load_investment_transactions(inv_rows)

    return {
        "_last_updated":          date.today().isoformat(),
        "_source":                "google_sheets",
        "accounts":               accounts,
        "summary":                summary,
        "investment_transactions": transactions,
        "holdings":               holdings,
    }

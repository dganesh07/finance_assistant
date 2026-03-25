"""
src/sheets_connector.py — Reads account data from Google Sheets.

Returns a dict with external account balances (EQ Bank, TFSA, other accounts)
for use by the portfolio agent (not yet wired into the spending context).

The Accounts tab is expected to have (at minimum) these columns:
  Account Name | Institution | Currency | Asset Class | Sub-Type |
  Balance | Include in Net Worth | Notes

Optional columns the connector will use if present:
  Base Rate  — annual interest rate without promo (%)
  Promo Rate — promotional interest rate (%)
  Promo End  — date promo expires (YYYY-MM-DD or any readable date string)

Column names are matched case-insensitively and flexibly — see _find_col().
Rows where "Include in Net Worth" is not Y/Yes/True are skipped.
TD Chequing is always skipped — its balance comes from the DB (account_balances).
401k / US retirement accounts are skipped (future dashboard feature).
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Column name aliases ────────────────────────────────────────────────────────
# Each entry is (canonical_name, [list of accepted header variants]).
# _find_col() tries each variant case-insensitively.

_COL_ALIASES: list[tuple[str, list[str]]] = [
    ("name",        ["Account Name", "Name", "Account"]),
    ("inst",        ["Institution", "Bank", "Brokerage"]),
    ("currency",    ["Currency", "CCY"]),
    ("asset_class", ["Asset Class", "Asset Type", "Class"]),
    ("subtype",     ["Sub-Type", "SubType", "Sub Type", "Type"]),
    ("balance",     ["Balance", "Current Balance", "Amount"]),
    # "Include in Net Worth? (Y/N)" — matched via startswith logic in _find_col
    ("include",     ["Include in Net Worth", "Include in Net Wo", "Include"]),
    ("notes",       ["Notes", "Note", "Comments"]),
    # Exact headers seen in the sheet: "Interest Rate (base)", "Interest Rate (promo)"
    ("base_rate",   ["Interest Rate (base)", "Base Rate", "Base Rate %",
                     "Interest Rate Base", "Base Interest", "Rate Base"]),
    ("promo_rate",  ["Interest Rate (promo)", "Promo Rate", "Promo Rate %",
                     "Interest Rate Promo", "Promo Interest", "Rate Promo"]),
    ("promo_end",   ["Promo End Date", "Promo End", "Promo Until",
                     "Promo Expiry", "Promo Expires"]),
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _find_col(headers: list[str], key: str) -> int | None:
    """
    Return 0-based index of a header column by canonical key, or None.

    Matching order (first match wins):
      1. Exact match (case-insensitive)
      2. Header starts with alias (handles trailing " (Y/N)", " ?", etc.)
      3. Alias starts with header (handles truncated headers)
    """
    aliases = next((a for k, a in _COL_ALIASES if k == key), [])
    clean = [h.strip().lower() for h in headers]
    for alias in aliases:
        a = alias.lower()
        # Pass 1: exact
        if a in clean:
            return clean.index(a)
    for alias in aliases:
        a = alias.lower()
        # Pass 2: header starts with alias (e.g. "include in net worth? (y/n)" starts with "include in net worth")
        for i, h in enumerate(clean):
            if h.startswith(a):
                return i
        # Pass 3: alias starts with header (e.g. truncated column)
        for i, h in enumerate(clean):
            if h and a.startswith(h):
                return i
    return None


def _cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    return row[idx].strip()


def _num(row: list[str], idx: int | None) -> float:
    raw = _cell(row, idx)
    if not raw:
        return 0.0
    try:
        return float(raw.replace("$", "").replace(",", "").replace("%", "").strip())
    except ValueError:
        return 0.0


def _parse_contribution_room(notes: str) -> float:
    """
    Try to extract a TFSA contribution room number from a notes string.
    Handles patterns like:
      "Contribution room: $9,000"
      "room remaining 5000"
      "9000 room left"
    """
    patterns = [
        r'room\s*remaining[:\s]+\$?([\d,]+)',
        r'contribution\s+room[:\s]+\$?([\d,]+)',
        r'room[:\s]+\$?([\d,]+)',
        r'\$?([\d,]+)\s+room',
    ]
    for pat in patterns:
        m = re.search(pat, notes, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                pass
    return 0.0


def _is_included(row: list[str], idx: int | None) -> bool:
    v = _cell(row, idx).upper()
    return v in ("Y", "YES", "TRUE", "1", "✓", "X")


# ── Main loader ────────────────────────────────────────────────────────────────

def load_from_sheets(sheet_id: str, creds_file: Path, tab_name: str = "Accounts") -> dict[str, Any]:
    """
    Read the Accounts tab and return a dict of external account balances.

    Returns empty dict on any failure so the caller can handle gracefully.
    """
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        logger.warning("gspread / google-auth not installed. Run: pip install gspread google-auth")
        return {}

    try:
        creds  = Credentials.from_service_account_file(
            str(creds_file),
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
        )
        client = gspread.authorize(creds)
        ws     = client.open_by_key(sheet_id).worksheet(tab_name)
        all_rows: list[list[str]] = ws.get_all_values()
    except Exception as exc:
        logger.warning("Google Sheets read failed: %s", exc)
        return {}

    if len(all_rows) < 2:
        logger.warning("Sheets Accounts tab has no data rows.")
        return {}

    headers   = all_rows[0]
    data_rows = all_rows[1:]

    # Build column index map
    cols = {key: _find_col(headers, key) for key, _ in _COL_ALIASES}

    result: dict[str, Any] = {
        "_last_updated": date.today().isoformat(),
        "_source":       "google_sheets",
        "eq_bank":       {},
        "gics":          [],
        "tfsa":          {"total_balance": 0.0, "invested_balance": 0.0,
                          "cash_balance": 0.0, "contribution_room_remaining": 0.0,
                          "notes": ""},
        "other_accounts": [],
        "upcoming_income": [],
    }

    tfsa_rows_found = 0

    for row in data_rows:
        name        = _cell(row, cols["name"])
        inst        = _cell(row, cols["inst"])
        asset_class = _cell(row, cols["asset_class"])
        subtype     = _cell(row, cols["subtype"])
        balance     = _num(row,  cols["balance"])
        notes       = _cell(row, cols["notes"])
        currency    = _cell(row, cols["currency"]) or "CAD"

        if not name:
            continue
        if not _is_included(row, cols["include"]):
            continue

        name_l  = name.lower()
        inst_l  = inst.lower()
        sub_l   = subtype.lower()
        class_l = asset_class.lower()

        # ── Skip: TD Chequing — balance comes from DB ──────────────────────────
        if "chequing" in name_l:
            continue

        # ── Skip: 401k / US retirement — future dashboard ─────────────────────
        if "401k" in name_l or "401(k)" in name_l or "roth" in name_l:
            continue

        # ── EQ Bank HISA ───────────────────────────────────────────────────────
        if "eq bank" in name_l or "eq bank" in inst_l:
            base_rate  = _num(row, cols["base_rate"])
            promo_rate = _num(row, cols["promo_rate"])
            promo_end  = _cell(row, cols["promo_end"])

            # If no dedicated promo column, try to parse from notes
            # Handles: "promo 2.75% until 2026-06-15" or "2.75% promo until June 2026"
            if promo_rate == 0 and notes:
                promo_match = re.search(r'promo\s+([\d.]+)%|(\d+\.?\d*)%\s+promo', notes, re.IGNORECASE)
                if promo_match:
                    promo_rate = float(promo_match.group(1) or promo_match.group(2))
                if not promo_end:
                    end_match = re.search(
                        r'until\s+(\d{4}-\d{2}-\d{2})',
                        notes, re.IGNORECASE
                    )
                    if end_match:
                        promo_end = end_match.group(1)

            result["eq_bank"] = {
                "savings_balance": balance,
                "hisa_rate_pct":   promo_rate if promo_rate > 0 else base_rate,
                "base_rate_pct":   base_rate,
                "promo_rate_pct":  promo_rate,
                "promo_end_date":  promo_end,
                "notes":           notes,
                "institution":     inst,
            }
            continue

        # ── TFSA accounts — check name, sub-type, OR asset class ──────────────
        is_tfsa_account = (
            "tfsa" in name_l
            or "tfsa" in sub_l
            or "tfsa" in class_l
            or "long term reg" in class_l
            or "long term reg" in sub_l
        )
        if is_tfsa_account:
            tfsa = result["tfsa"]
            tfsa["total_balance"] += balance
            tfsa_rows_found += 1

            is_cash = any(x in sub_l for x in ("cash", "savings", "hisa")) or "cash" in name_l
            if is_cash:
                tfsa["cash_balance"] += balance
            else:
                tfsa["invested_balance"] += balance

            # Extract contribution room from notes if present
            room = _parse_contribution_room(notes)
            if room > 0:
                tfsa["contribution_room_remaining"] = room

            if notes and notes not in tfsa.get("notes", ""):
                existing = tfsa.get("notes", "")
                tfsa["notes"] = f"{existing} | {notes}".strip(" |") if existing else notes

            continue

        # ── Skip zero-balance accounts ─────────────────────────────────────────
        if balance == 0:
            continue

        # ── Everything else with Y in Include in Net Worth ─────────────────────
        result["other_accounts"].append({
            "institution": inst,
            "nickname":    name,
            "balance":     balance,
            "currency":    currency,
            "notes":       notes,
        })

    # Clean up empty TFSA
    if tfsa_rows_found == 0:
        result["tfsa"] = {}

    return result

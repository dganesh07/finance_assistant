"""
src/parser_td.py — TD Bank statement parser (CSV + PDF).

Organised in 4 sections so it's easy to use as a template for new banks:

  SECTION 1 — DATES
    TD-specific date formats:
      • Transaction dates: "FEB02", "FEB 27" (month-abbrev + day, no year)
      • Chequing header:   "JAN30/26" or "JAN 30/26" (MMMDD/YY)
      • CC statement:      "December30,2025" or "January 27, 2026" (full month name)

  SECTION 2 — ACCOUNT DETECTION
    Infer account label (chequing / creditcard / savings / loc) from filename.

  SECTION 3 — TRANSACTIONS
    3a. TD CSV  — headerless (Date,Desc,Debit,Credit,Balance) and headered
    3b. TD Chequing PDF — pdfplumber table extraction
    3c. TD Visa CC PDF  — raw text extraction (no tables in CC PDFs)

  SECTION 4 — ACCOUNT BALANCE & STATEMENT DATES
    Extract official statement period and opening/closing balances.
    Used by parse_new_statements() for balance reconciliation and runway calc.

To add a new bank (e.g. EQ Bank):
  1. Copy this file as src/parser_eq.py
  2. Replace each section with the new bank's logic
  3. Register it in parse_new_statements() in parser_core.py
"""

import csv
import logging
import re
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import pdfplumber
from rich.console import Console

# Allow direct import when project root is not in sys.path
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from src.parser_core import normalise_date, scrub_description

console = Console()
log     = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — DATES
# ═══════════════════════════════════════════════════════════════════════════════

# Transaction date format in TD PDFs: "FEB02" or "FEB 27"
_TD_DATE_RE = re.compile(
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*(\d{1,2})$",
    re.IGNORECASE,
)


def _normalise_td_date(raw: str) -> Optional[str]:
    """
    Parse TD PDF transaction date 'FEB02' or 'FEB 27' → 'YYYY-MM-DD'.
    Year is inferred: future date → roll back one year.
    """
    raw = raw.strip()
    raw = re.sub(r"([A-Za-z]{3})(\d{1,2})", r"\1 \2", raw)  # "FEB02" → "FEB 02"
    return normalise_date(raw)


# Chequing header dates: "JAN30/26", "FEB 27/26"
_MONTH_ABBR: dict[str, int] = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def _parse_td_header_date(raw: str) -> Optional[str]:
    """
    Parse a TD chequing header date 'MMMDD/YY' or 'MMM DD/YY' → 'YYYY-MM-DD'.

    Examples:
      'JAN30/26'  → '2026-01-30'
      'FEB 27/26' → '2026-02-27'
    """
    raw = raw.strip().upper()
    m   = re.match(r"([A-Z]{3})\s*(\d{1,2})/(\d{2})$", raw)
    if not m:
        return None
    month_num = _MONTH_ABBR.get(m.group(1))
    if not month_num:
        return None
    day  = int(m.group(2))
    year = 2000 + int(m.group(3))
    try:
        return f"{year}-{month_num:02d}-{day:02d}"
    except (ValueError, OverflowError):
        return None


# CC statement period dates: "December30,2025" or "January 27, 2026"
_CC_MONTH_NAMES: dict[str, int] = {
    "january": 1, "february": 2, "march": 3,    "april": 4,
    "may": 5,     "june": 6,     "july": 7,     "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def _parse_cc_date(raw: str) -> Optional[str]:
    """
    Parse a TD CC statement date 'MonthNameDD,YYYY' (pdfplumber may concatenate).

    Examples:
      'December30,2025'   → '2025-12-30'
      'January 27, 2026'  → '2026-01-27'
    """
    m = re.match(r"([A-Za-z]+)\s*(\d{1,2})\s*,\s*(\d{4})", raw.strip())
    if not m:
        return None
    month_num = _CC_MONTH_NAMES.get(m.group(1).lower())
    if not month_num:
        return None
    day  = int(m.group(2))
    year = int(m.group(3))
    try:
        return f"{year}-{month_num:02d}-{day:02d}"
    except (ValueError, OverflowError):
        return None


def _parse_dollar(s: str) -> Optional[float]:
    """Parse '$1,234.56' or '1234.56' → float, or None on failure."""
    s = re.sub(r"[$, ]", "", (s or "").strip())
    try:
        return float(s) if s else None
    except ValueError:
        return None


# Statement period header patterns (used in Section 4)
_CHQ_PERIOD_RE = re.compile(
    r"([A-Z]{3}\s*\d{1,2}/\d{2})\s*-\s*([A-Z]{3}\s*\d{1,2}/\d{2})",
    re.IGNORECASE,
)
_CC_PERIOD_RE = re.compile(
    r"(?:STATEMENT|BILLING|ACCOUNT)\s*PERIOD\s*:?\s*"
    r"([A-Za-z]+\s*\d{1,2}\s*,\s*\d{4})"   # start: "December30,2025"
    r"\s*to\s*"
    r"([A-Za-z]+\s*\d{1,2}\s*,\s*\d{4})",  # end:   "January27,2026"
    re.IGNORECASE,
)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — ACCOUNT DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

# Filename keywords → canonical account label.
# Add entries here as you support more TD account types.
_ACCOUNT_KEYWORDS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"visa",               re.IGNORECASE), "creditcard"),
    (re.compile(r"mastercard|mc",      re.IGNORECASE), "creditcard"),
    (re.compile(r"chequ|checking",     re.IGNORECASE), "chequing"),
    (re.compile(r"saving",             re.IGNORECASE), "savings"),
    (re.compile(r"line.of.credit|loc", re.IGNORECASE), "loc"),
]


def detect_account(filename: str) -> str:
    """
    Infer account label from a TD statement filename.

    Examples:
      'MY_CHEQUING_ACCOUNT_...'  → 'chequing'
      'MY_VISA_CARD_...'         → 'creditcard'

    Returns 'unknown' if no pattern matches.
    """
    for pattern, label in _ACCOUNT_KEYWORDS:
        if pattern.search(filename):
            return label
    return "unknown"


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — TRANSACTIONS
# ═══════════════════════════════════════════════════════════════════════════════

# ── 3a. TD CSV ─────────────────────────────────────────────────────────────────

# Column-name aliases by logical field (case-insensitive lookup).
_CSV_DATE_COLS   = ["date", "transaction date", "trans date", "posted date", "effective date"]
_CSV_DESC_COLS   = ["description", "memo", "transaction description", "details", "narration", "payee"]
_CSV_DEBIT_COLS  = ["debit", "withdrawals", "withdrawal", "debit amount", "amount debit"]
_CSV_CREDIT_COLS = ["credit", "deposits", "deposit", "credit amount", "amount credit"]
_CSV_AMOUNT_COLS = ["amount", "transaction amount", "net amount"]

# TD chequing/savings CSV has NO header row — fixed 5-column order.
_TD_HEADERLESS_COLS = ["date", "description", "debit", "credit", "balance"]


def _find_col(df_cols_lower: list[str], candidates: list[str]) -> Optional[str]:
    """Return the first candidate that exists in df_cols_lower (case-insensitive)."""
    for c in candidates:
        if c in df_cols_lower:
            return c
    return None


def _is_td_headerless(file_path: Path) -> bool:
    """
    Return True if the CSV looks like a TD headerless export
    (first cell is a date, not a column label).
    """
    try:
        with open(file_path, newline="", encoding="utf-8-sig") as f:
            first_line = f.readline().strip()
        if not first_line:
            return False
        first_cell = first_line.split(",")[0].strip().strip('"')
        return normalise_date(first_cell) is not None
    except OSError:
        return False


def parse_csv(file_path: Path) -> list[dict]:
    """
    Parse a TD CSV bank statement export into normalised transaction dicts.

    Supports:
      • TD headerless (Date, Description, Debit, Credit, Balance) — no column names
      • TD headered (Date, Description, Debit, Credit)
      • Generic single-amount column (positive = credit, negative = debit)
      • Any delimiter (auto-sniffed)

    Returns: [{date, description, amount, type}, ...]
    """
    transactions = []

    try:
        with open(file_path, newline="", encoding="utf-8-sig") as f:
            sample  = f.read(4096)
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        sep     = dialect.delimiter
    except csv.Error:
        sep = ","

    try:
        if _is_td_headerless(file_path):
            df = pd.read_csv(file_path, sep=sep, encoding="utf-8-sig",
                             header=None, names=_TD_HEADERLESS_COLS,
                             skip_blank_lines=True, dtype=str)
        else:
            df = pd.read_csv(file_path, sep=sep, encoding="utf-8-sig",
                             skip_blank_lines=True, dtype=str)
    except pd.errors.EmptyDataError:
        log.debug("parse_csv: %s is empty — skipping", file_path.name)
        return []

    df.dropna(how="all", inplace=True)

    col_map    = {c.lower().strip(): c for c in df.columns}
    cols_lower = list(col_map.keys())

    date_col   = _find_col(cols_lower, _CSV_DATE_COLS)
    desc_col   = _find_col(cols_lower, _CSV_DESC_COLS)
    debit_col  = _find_col(cols_lower, _CSV_DEBIT_COLS)
    credit_col = _find_col(cols_lower, _CSV_CREDIT_COLS)
    amount_col = _find_col(cols_lower, _CSV_AMOUNT_COLS)

    if not date_col or not desc_col:
        console.print(
            f"[yellow]  Warning: could not detect date/description columns in "
            f"{file_path.name}. Found: {list(df.columns)}[/yellow]"
        )
        return []

    for idx, row in df.iterrows():
        try:
            raw_date = str(row[col_map[date_col]]).strip()
            date     = normalise_date(raw_date)
            if not date:
                log.debug("Row %s: unparseable date %r — skipped", idx, raw_date)
                continue

            description = scrub_description(str(row[col_map[desc_col]]).strip())
            if not description or description.lower() in ("nan", ""):
                continue

            amount   = None
            txn_type = None

            if debit_col and credit_col:
                raw_debit  = str(row.get(col_map[debit_col],  "")).strip()
                raw_credit = str(row.get(col_map[credit_col], "")).strip()

                def _parse_amount(s: str) -> Optional[float]:
                    s = re.sub(r"[$, ]", "", s)
                    try:
                        return float(s) if s and s.lower() != "nan" else None
                    except ValueError:
                        return None

                debit_val  = _parse_amount(raw_debit)
                credit_val = _parse_amount(raw_credit)

                if debit_val:
                    amount, txn_type = debit_val, "debit"
                elif credit_val:
                    amount, txn_type = credit_val, "credit"

            elif amount_col:
                raw_amt = re.sub(r"[$, ]", "", str(row[col_map[amount_col]]).strip())
                try:
                    val = float(raw_amt)
                    if val < 0:
                        amount, txn_type = abs(val), "debit"
                    else:
                        amount, txn_type = val, "credit"
                except ValueError:
                    pass

            if amount is None:
                log.debug("Row %s: could not determine amount — skipped", idx)
                continue

            transactions.append({
                "date":        date,
                "description": description,
                "amount":      round(amount, 2),
                "type":        txn_type,
            })

        except Exception as e:
            log.debug("Row %s parse error: %s", idx, e)
            continue

    return transactions


# ── 3b. TD Chequing PDF (table-based) ─────────────────────────────────────────

_AMOUNT_RE = re.compile(r"-?\$?[\d,]+\.\d{2}")

# Descriptions to skip in the TD transaction table
_TD_SKIP_DESCRIPTIONS = {
    "startingbalance", "starting balance",
    "closingbalance",  "closing balance",
    "balanceforward",  "balance forward",
    "",
}


def _is_td_transaction_table(header_row: list[str]) -> bool:
    """
    Return True if this table's header looks like a TD transaction table:
    must contain 'Description' AND ('Withdrawals' OR 'Deposits') AND 'Date'.
    """
    joined = " ".join(header_row).lower()
    return (
        "description" in joined
        and ("withdrawals" in joined or "deposits" in joined)
        and "date" in joined
    )


def _parse_td_table(table: list[list]) -> tuple[list[dict], int]:
    """
    Parse a TD chequing/savings transaction table extracted by pdfplumber.

    TD table structure:
      Header: ['Description', 'Withdrawals', 'Deposits', 'Date', 'Balance']

    Handles:
      • Multi-transaction cells (pdfplumber merges rows with '\\n')
      • Split-column merge (one credit + one debit packed into one cell)
      • Fee-row + rebate-row merging (dep_carry logic)
      • Page-total bleed (extra running totals injected by TD's PDF renderer)
      • Balance-column bleed (wd value equals running balance)

    Returns (transactions, dropped) — dropped counts rows skipped unexpectedly.
    """
    results = []
    dropped = 0
    rows    = [[str(c or "").strip() for c in row] for row in table]

    if not rows:
        return results, dropped

    header = [c.lower() for c in rows[0]]
    try:
        desc_idx = header.index("description")
        date_idx = header.index("date")
    except ValueError:
        return results, dropped

    wd_idx  = next((i for i, c in enumerate(header) if "withdrawal" in c), None)
    dep_idx = next((i for i, c in enumerate(header) if "deposit"    in c), None)
    bal_idx = next((i for i, c in enumerate(header) if "balance"    in c), None)

    def _pad(lst, length):
        return lst + [""] * (length - len(lst))

    for row in rows[1:]:
        while len(row) < len(header):
            row.append("")

        raw_desc = row[desc_idx]
        raw_date = row[date_idx]

        if not raw_desc and not raw_date:
            continue

        descs = [d.strip() for d in raw_desc.split("\n")]
        dates = [d.strip() for d in raw_date.split("\n")]
        wds   = [d.strip() for d in (row[wd_idx].split("\n")  if wd_idx  is not None else [""])]
        deps  = [d.strip() for d in (row[dep_idx].split("\n") if dep_idx is not None else [""])]
        bals  = [d.strip() for d in (row[bal_idx].split("\n") if bal_idx is not None else [""])]

        _n_wd_vals  = sum(1 for x in wds  if re.sub(r"[$, ]", "", x))
        _n_dep_vals = sum(1 for x in deps if re.sub(r"[$, ]", "", x))
        _bals_pre_pad = bals.copy()

        # n is driven by descriptions/dates — NOT by amount columns.
        # The last row on each TD page has extra page-total values that would
        # create phantom sub-entries if included in n.
        n = max(len(descs), len(dates))

        # Page-total bleed: more wd values than descriptions → discard surpluses
        if len(wds) > n:
            wds  = wds[:n]
            deps = [""] * n

        descs = _pad(descs, n)
        dates = _pad(dates, n)
        wds   = _pad(wds,   n)
        deps  = _pad(deps,  n)
        bals  = _pad(bals,  n)

        # Split-column merge reorder: when n=2 with exactly 1 wd AND 1 dep,
        # pdfplumber packed two opposite-type transactions into one PDF row.
        # Use balance deltas to determine which came first.
        if n == 2 and _n_wd_vals == 1 and _n_dep_vals == 1:
            _bv = []
            for _b in _bals_pre_pad:
                _bc = re.sub(r"[$, ]", "", _b)
                if _bc:
                    try:
                        _bv.append(float(_bc))
                    except ValueError:
                        pass
            _credit_first = len(_bv) >= 2 and (_bv[1] - _bv[0]) < 0
            if _credit_first:
                wds  = ["", wds[0]]
                deps = [deps[0], ""]
            else:
                wds  = [wds[0], ""]
                deps = ["", deps[0]]

        dep_carry:      Optional[str] = None
        split_wd_carry: Optional[str] = None

        for desc, raw_d, wd, dep, bal in zip(descs, dates, wds, deps, bals):
            if desc.lower().replace(" ", "") in _TD_SKIP_DESCRIPTIONS:
                continue
            if not raw_d:
                continue

            date = _normalise_td_date(raw_d)
            if not date:
                continue

            amount   = None
            txn_type = None

            wd_clean  = re.sub(r"[$, ]", "", wd)
            dep_clean = re.sub(r"[$, ]", "", dep)
            bal_clean = re.sub(r"[$, ]", "", bal)

            # Guard: balance-column bleed — wd equals running balance
            if wd_clean and wd_clean == bal_clean:
                wd_clean = ""

            try:
                wd_val = float(wd_clean) if wd_clean else 0.0
            except ValueError:
                wd_val = 0.0
            try:
                dep_val = float(dep_clean) if dep_clean else 0.0
            except ValueError:
                dep_val = 0.0

            if split_wd_carry is not None:
                if not wd_clean and not dep_clean:
                    wd_clean = split_wd_carry
                    wd_val   = float(wd_clean)
                split_wd_carry = None

            if wd_val > 0 and dep_val > 0:
                if wd_clean == dep_clean:
                    dep_carry = dep_clean
                    dep_clean = ""
                elif dep_carry is not None:
                    wd_clean  = ""
                    dep_clean = dep_carry
                    dep_carry = None
                else:
                    split_wd_carry = wd_clean
                    wd_clean = ""
            else:
                if dep_carry is not None and not wd_clean and not dep_clean:
                    dep_clean = dep_carry
                    dep_val   = float(dep_carry)
                dep_carry = None

            try:
                if wd_clean and float(wd_clean) > 0:
                    amount, txn_type = round(float(wd_clean), 2), "debit"
                elif dep_clean and float(dep_clean) > 0:
                    amount, txn_type = round(float(dep_clean), 2), "credit"
            except ValueError:
                dropped += 1
                console.print(
                    f"  [yellow]⚠ drop:[/yellow] unparseable amount for "
                    f"[dim]{desc[:50]!r}[/dim] on {date}"
                )
                continue

            if amount is None:
                if desc and desc.lower().replace(" ", "") not in _TD_SKIP_DESCRIPTIONS:
                    dropped += 1
                    console.print(
                        f"  [yellow]⚠ drop:[/yellow] no amount found for "
                        f"[dim]{desc[:50]!r}[/dim] on {date}"
                    )
                continue

            results.append({
                "date":        date,
                "description": scrub_description(desc),
                "amount":      amount,
                "type":        txn_type,
            })

    return results, dropped


# Generic text fallback (used when no TD table found on a chequing page)
_DATE_RE = re.compile(
    r"\b(?:\d{1,2}[-/]\d{1,2}(?:[-/]\d{2,4})?|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:,?\s+\d{4})?|"
    r"\d{4}[-/]\d{2}[-/]\d{2})\b",
    re.IGNORECASE,
)


def _extract_from_text(text: str) -> list[dict]:
    """Fallback: scan raw page text for date + amount patterns."""
    results = []
    for line in text.splitlines():
        line = line.strip()
        if len(line) < 5:
            continue
        date_m   = _DATE_RE.search(line)
        amount_m = _AMOUNT_RE.search(line)
        if not date_m or not amount_m:
            continue
        date = normalise_date(date_m.group())
        if not date:
            continue
        start       = date_m.end()
        end         = amount_m.start()
        description = line[start:end].strip(" -|:")
        if not description:
            description = line.replace(date_m.group(), "").replace(amount_m.group(), "").strip()
        if not description:
            continue
        try:
            val = float(re.sub(r"[$, ]", "", amount_m.group()))
        except ValueError:
            continue
        results.append({
            "date":        date,
            "description": scrub_description(description),
            "amount":      round(abs(val), 2),
            "type":        "debit",
        })
    return results


# ── 3c. TD Visa CC PDF (raw text, no tables) ───────────────────────────────────
#
# TD Visa transactions appear as raw text lines:
#   MMMDD MMMDD MERCHANT-NAME $X.XX
#   MMMDD MMMDD PAYMENT       -$X.XX   ← negative = payment/refund
#
# pdfplumber quirks:
#   • Page-1 may have side-panel text after the amount — regex stops at first $X.XX
#   • Multi-line merchant names → only first line captured, continuation silently dropped

_TD_VISA_TXN_RE = re.compile(
    r"^((?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s*\d{1,2})"
    r"\s+"
    r"(?:(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s*\d{1,2})"
    r"\s+"
    r"(.+?)"
    r"\s+"
    r"(-?\$[\d,]+\.\d{2})",
    re.IGNORECASE | re.MULTILINE,
)

# Lines to skip — account summaries, headers, totals, boilerplate
_TD_VISA_SKIP_RE = re.compile(
    r"STATEMENT\s+(DATE|PERIOD|BALANCE|SUMMARY)|"
    r"ACCOUNT\s+(NUMBER|SUMMARY)|"
    r"CREDIT\s+LIMIT|"
    r"MINIMUM\s+PAYMENT|"
    r"PAYMENT\s+DUE\s+DATE|"
    r"OPENING\s+BALANCE|"
    r"CLOSING\s+BALANCE|"
    r"NEW\s+BALANCE|"
    r"TOTAL\s*(NEW\s*)?BALANCE|"
    r"TOTAL\s+(CREDITS?|DEBITS?|CHARGES?)|"
    r"PAYMENTS?\s*[&and]+\s*CREDITS?|"
    r"PURCHASES?\s*[&and]+\s*OTHER\s*CHARGES?|"
    r"CASH\s*ADVANCES?|"
    r"SUB-?TOTAL|"
    r"INTEREST\s*(CHARGED|RATE|FREE|\$)|"
    r"ANNUAL\s+FEE|"
    r"PREVIOUS\s*(?:STATEMENT\s*)?BALANCE|"
    r"^\s*FEES?\s|"
    r"TD\s+CANADA\s+TRUST|"
    r"^\s*\d{1,4}\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_SUSPECT_CC_AMOUNT_RE = re.compile(r"-?\$[\d,]+\.\d{2}")


def _is_td_visa_text(text: str) -> bool:
    """Return True if the text contains the double-MMMDD pattern of TD Visa transactions."""
    return bool(_TD_VISA_TXN_RE.search(text))


def _parse_td_visa_text(text: str) -> tuple[list[dict], int]:
    """
    Parse TD Visa CC statement raw text (no tables).

    Each transaction line: MMMDD MMMDD DESCRIPTION $AMOUNT
    Negative amounts → type='credit' (payment/refund).
    Positive amounts → type='debit'  (purchase/charge).

    Returns (transactions, dropped).
    """
    results = []
    dropped = 0

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        m = _TD_VISA_TXN_RE.match(line)
        if not m:
            if _SUSPECT_CC_AMOUNT_RE.search(line) and not _TD_VISA_SKIP_RE.search(line):
                dropped += 1
                console.print(
                    f"  [yellow]⚠ drop:[/yellow] line has amount but no date match "
                    f"[dim]{line[:70]!r}[/dim]"
                )
            continue

        raw_date_str = m.group(1)
        description  = m.group(2).strip()
        raw_amount   = m.group(3)

        date = _normalise_td_date(raw_date_str)
        if not date:
            continue

        try:
            val = float(re.sub(r"[$,]", "", raw_amount))
        except ValueError:
            continue

        if val == 0 or not description:
            continue

        amount   = round(abs(val), 2)
        txn_type = "credit" if val < 0 else "debit"

        results.append({
            "date":        date,
            "description": scrub_description(description),
            "amount":      amount,
            "type":        txn_type,
        })

    if not results and not dropped:
        if _SUSPECT_CC_AMOUNT_RE.search(text):
            console.print(
                "  [yellow]⚠ warning:[/yellow] CC page matched no transactions "
                "but contains dollar amounts — statement format may have changed"
            )

    return results, dropped


def _extract_text_spaced(page) -> str:
    """
    Extract CC page text with tight x_tolerance=1 to restore merchant name spaces.

    pdfplumber default (x_tolerance=3) collapses gaps, turning
    "WAVES COFFEE CITY POINT" into "WAVESCOFFEECITYPOINT".
    x_tolerance=1 is only safe once we've confirmed the page is CC-format —
    it can garble other banks' PDFs with tight character kerning.
    """
    return page.extract_text(x_tolerance=1, y_tolerance=5) or ""


def parse_pdf(file_path: Path) -> tuple[list[dict], int]:
    """
    Extract transactions from a TD PDF statement using pdfplumber.

    Strategy (per page):
      1. If pdfplumber finds tables → check each for TD transaction table format
         → _parse_td_table()
      2. If no TD transaction table found → fall through to text parsing:
         a. CC statement confirmed → re-extract with tight x_tolerance
            → _parse_td_visa_text()
         b. No tables at all → generic _extract_from_text() fallback

    Returns (transactions, total_dropped).
    """
    transactions  = []
    total_dropped = 0

    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                tables              = page.extract_tables()
                found_td_txn_table  = False

                if tables:
                    for table in tables:
                        if not table:
                            continue
                        header = [str(c or "").strip() for c in table[0]]
                        if _is_td_transaction_table(header):
                            rows, d = _parse_td_table(table)
                            transactions.extend(rows)
                            total_dropped += d
                            found_td_txn_table = True

                if not found_td_txn_table:
                    default_text = page.extract_text() or ""
                    if _is_td_visa_text(default_text):
                        spaced_text = _extract_text_spaced(page)
                        rows, d = _parse_td_visa_text(spaced_text)
                        transactions.extend(rows)
                        total_dropped += d
                    elif not tables:
                        transactions.extend(_extract_from_text(default_text))

    except Exception as e:
        console.print(f"[red]  PDF parse error for {file_path.name}: {e}[/red]")

    if not transactions:
        console.print(
            f"  [yellow]⚠ warning:[/yellow] no transactions extracted from "
            f"[dim]{file_path.name}[/dim] — run [cyan]--inspect[/cyan] to diagnose"
        )

    return transactions, total_dropped


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — ACCOUNT BALANCE & STATEMENT DATES
# ═══════════════════════════════════════════════════════════════════════════════

# CC summary patterns — used in verify_statement()
_CC_CHARGES_RE  = re.compile(r"Purchases?\s*[&and]+\s*Other\s*Charges?\s+\$?([\d,]+\.\d{2})", re.IGNORECASE)
_CC_PAYMENTS_RE = re.compile(r"Payments?\s*[&and]+\s*Credits?\s+\$?([\d,]+\.\d{2})",          re.IGNORECASE)
_CC_NEW_BAL_RE  = re.compile(r"NEW\s*BALANCE\s+\$?([\d,]+\.\d{2})",                           re.IGNORECASE)

# Chequing balance patterns
_CHQ_START_BAL_RE = re.compile(r"STARTINGBALANCE|STARTING\s+BALANCE", re.IGNORECASE)
_DOLLAR_RE        = re.compile(r"\$?([\d,]+\.\d{2})")


def extract_statement_dates(
    file_path: Path, account: str
) -> tuple[Optional[str], Optional[str]]:
    """
    Extract the official statement period from the first two pages of a TD PDF.

    TD chequing:    "BranchNo. Account No. JAN30/26-FEB 27/26"
    TD credit card: "STATEMENT PERIOD December30,2025 to January27,2026"
                    (also handles BILLING PERIOD and ACCOUNT PERIOD)

    Returns (start_iso, end_iso) as YYYY-MM-DD, or (None, None) on failure.
    """
    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages[:2]:
                text = page.extract_text() or ""

                if account == "chequing":
                    m = _CHQ_PERIOD_RE.search(text)
                    if m:
                        start = _parse_td_header_date(m.group(1))
                        end   = _parse_td_header_date(m.group(2))
                        if start and end:
                            return start, end

                elif account == "creditcard":
                    m = _CC_PERIOD_RE.search(text)
                    if m:
                        start = _parse_cc_date(m.group(1).strip())
                        end   = _parse_cc_date(m.group(2).strip())
                        if start and end:
                            return start, end

    except Exception as e:
        log.debug("extract_statement_dates failed for %s: %s", file_path.name, e)

    return None, None


def verify_statement(file_path: Path, account: str) -> dict:
    """
    Open a TD statement PDF and extract official summary totals for reconciliation.

    Returns:
      {
        account, file,
        # CC fields
        expected_charges:  float | None,   # Purchases & Other Charges
        expected_payments: float | None,   # Payments & Credits
        expected_new_bal:  float | None,   # New Balance
        # Chequing fields
        opening_balance:   float | None,
        closing_balance:   float | None,
      }
    None means the value wasn't found in the statement.
    """
    result: dict = {
        "account":           account,
        "file":              file_path.name,
        "expected_charges":  None,
        "expected_payments": None,
        "expected_new_bal":  None,
        "opening_balance":   None,
        "closing_balance":   None,
    }

    try:
        with pdfplumber.open(file_path) as pdf:
            full_text = "\n".join((page.extract_text() or "") for page in pdf.pages)

            if account == "creditcard":
                m = _CC_CHARGES_RE.search(full_text)
                if m:
                    result["expected_charges"] = _parse_dollar(m.group(1))
                m = _CC_PAYMENTS_RE.search(full_text)
                if m:
                    result["expected_payments"] = _parse_dollar(m.group(1))
                m = _CC_NEW_BAL_RE.search(full_text)
                if m:
                    result["expected_new_bal"] = _parse_dollar(m.group(1))

            elif account == "chequing":
                # Opening balance → STARTINGBALANCE row
                # Closing balance → last non-empty balance value across all pages
                for page in pdf.pages:
                    for table in (page.extract_tables() or []):
                        if not table or not _is_td_transaction_table(
                            [str(c or "").strip() for c in table[0]]
                        ):
                            continue
                        header = [str(c or "").lower().strip() for c in table[0]]
                        try:
                            bal_idx = next(i for i, h in enumerate(header) if "balance" in h)
                        except StopIteration:
                            continue

                        for row in table[1:]:
                            if not row:
                                continue
                            cells    = [str(c or "").strip() for c in row]
                            desc     = cells[0] if cells else ""
                            bal_cell = cells[bal_idx] if bal_idx < len(cells) else ""

                            descs = [d.strip() for d in desc.split("\n")]
                            bals  = [b.strip() for b in bal_cell.split("\n")]

                            for d, b in zip(descs, bals):
                                if _CHQ_START_BAL_RE.search(d) and result["opening_balance"] is None:
                                    result["opening_balance"] = _parse_dollar(
                                        re.sub(r"[$,]", "", b)
                                    )
                                b_val = _parse_dollar(re.sub(r"[$,]", "", b))
                                if b_val is not None:
                                    result["closing_balance"] = b_val

    except Exception as e:
        log.debug("verify_statement failed for %s: %s", file_path.name, e)

    return result


# ── Debug utility ──────────────────────────────────────────────────────────────

def inspect_pdf(file_path: Path) -> None:
    """
    Debug tool: print exactly what pdfplumber extracts from each page
    (tables and raw text) so the parser logic can be tuned.

    Usage:
        python src/parser.py --inspect data/statements/MY_STATEMENT.pdf
    """
    console.rule(f"[bold yellow]Inspecting: {file_path.name}[/bold yellow]")

    with pdfplumber.open(file_path) as pdf:
        console.print(f"[dim]Pages: {len(pdf.pages)}[/dim]\n")

        for i, page in enumerate(pdf.pages, 1):
            console.rule(f"[cyan]Page {i}[/cyan]")
            tables = page.extract_tables()
            if tables:
                console.print(f"[green]Found {len(tables)} table(s):[/green]")
                for t_idx, table in enumerate(tables, 1):
                    console.print(f"\n  [bold]Table {t_idx}[/bold] ({len(table)} rows):")
                    for r_idx, row in enumerate(table):
                        console.print(f"    row {r_idx:>2}: {row}")
            else:
                console.print("[yellow]No tables found — raw text:[/yellow]")
                text = page.extract_text() or ""
                for line in text.splitlines():
                    if line.strip():
                        console.print(f"  {line}")

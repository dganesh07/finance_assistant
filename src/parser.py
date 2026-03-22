"""
src/parser.py — PDF + CSV statement parser.

Flow per file:
  1. Check if already imported (source_file in DB) → skip if yes
  2. Route to parse_csv() or parse_pdf()
  3. Normalize every row to: {date, description, amount, type, source_file, hash}
  4. Insert only rows whose hash doesn't exist yet (dedup)
  5. Return a summary dict per file

Run standalone test:
  python src/parser.py --test
"""

import argparse
import csv
import hashlib
import json
import logging
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Optional

import pandas as pd
import pdfplumber
from dateutil import parser as dateutil_parser
from rich.console import Console
from rich.table import Table

# Allow running as a script from project root
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import CORRECTIONS_FILE, DB_PATH, STATEMENTS_DIR

console = Console()
log = logging.getLogger(__name__)


# ── Date normalisation ────────────────────────────────────────────────────────

def normalise_date(raw: str) -> Optional[str]:
    """
    Parse any bank date string into ISO YYYY-MM-DD.

    Handles formats like:
      • "Mar 15, 2024"   • "03/15/2024"  • "2024-03-15"
      • "15-Mar-24"      • "03/15"  (no year — inferred from today)

    Returns None if the string cannot be parsed.
    """
    raw = raw.strip()
    if not raw:
        return None

    from datetime import date
    today = date.today()

    try:
        # dateutil handles almost everything; dayfirst=False keeps MM/DD for North American banks
        dt = dateutil_parser.parse(raw, dayfirst=False)
    except (ValueError, OverflowError):
        try:
            # Second attempt with dayfirst=True for DD/MM formats
            dt = dateutil_parser.parse(raw, dayfirst=True)
        except (ValueError, OverflowError):
            return None

    # If the raw string contained no year, dateutil defaults to current year.
    # Guard: if that gives a future date, roll back one year (handles Dec statements
    # imported in January).
    no_year = not re.search(r"\b(19|20)\d{2}\b", raw) and len(re.findall(r"\d+", raw)) <= 2
    if no_year and dt.date() > today:
        dt = dt.replace(year=dt.year - 1)

    return dt.strftime("%Y-%m-%d")


# ── Hash ─────────────────────────────────────────────────────────────────────

def compute_hash(date: str, description: str, amount: float, occurrence: int = 0) -> str:
    """MD5 fingerprint of date + description + amount + occurrence for deduplication.

    occurrence allows two legitimately identical transactions in the same file
    (e.g. two parking charges for the same amount on the same day) to coexist
    in the DB without being treated as duplicates of each other.
    """
    raw = f"{date}|{description}|{amount:.2f}|{occurrence}"
    return hashlib.md5(raw.encode()).hexdigest()


# ── Already-imported guard ────────────────────────────────────────────────────

def is_already_imported(conn: sqlite3.Connection, filename: str) -> bool:
    """
    Return True if any transaction row already carries this source_file.
    If yes, the whole file was previously imported — skip it entirely.
    """
    row = conn.execute(
        "SELECT 1 FROM transactions WHERE source_file = ? LIMIT 1",
        (filename,)
    ).fetchone()
    return row is not None


# ── Personal info scrubbing ───────────────────────────────────────────────────

# Patterns in descriptions that reveal real names (e-transfers, cheques).
# These are replaced with a generic placeholder.
_SCRUB_PATTERNS = [
    # INTERAC e-Transfer SENT TO FIRSTNAME LASTNAME
    (re.compile(r"(INTERAC\s+e-?TRANSFER\s+(?:SENT\s+TO|RECEIVED\s+FROM))\s+[\w\s\-'\.]{2,40}",
                re.IGNORECASE),
     r"\1 [RECIPIENT]"),
    # e-TFR TO / FROM name
    (re.compile(r"(e-?TFR\s+(?:TO|FROM))\s+[\w\s\-'\.]{2,30}", re.IGNORECASE),
     r"\1 [RECIPIENT]"),
    # "Cheque #1234 to John Smith" style
    (re.compile(r"(CHEQUE\s+#?\d+\s+(?:TO|FROM|PAYABLE\s+TO))\s+[\w\s\-'\.]{2,40}",
                re.IGNORECASE),
     r"\1 [PAYEE]"),
    # TD chequing transaction-type suffixes with embedded amount: "MERCHANT 9.99_V" → "MERCHANT"
    (re.compile(r"\s+[\d]+\.[\d]{2}_[VF]\s*$", re.IGNORECASE), ""),
    # TD chequing transaction-type suffixes without amount: "Amazon.ca _V" → "Amazon.ca"
    (re.compile(r"\s+_[VF]\s*$", re.IGNORECASE), ""),
]


def scrub_description(desc: str) -> str:
    """
    Remove personal names from transaction descriptions.

    Targets e-transfer recipient names and cheque payee names.
    All other descriptions (merchant names, bill payments) are left unchanged.
    """
    for pattern, replacement in _SCRUB_PATTERNS:
        desc = pattern.sub(replacement, desc)
    return desc


# ── CSV parsing ───────────────────────────────────────────────────────────────

# Column-name aliases keyed by logical field.
# The parser tries each list in order and uses the first match it finds.
_CSV_DATE_COLS   = ["date", "transaction date", "trans date", "posted date", "effective date"]
_CSV_DESC_COLS   = ["description", "memo", "transaction description", "details", "narration", "payee"]
_CSV_DEBIT_COLS  = ["debit", "withdrawals", "withdrawal", "debit amount", "amount debit"]
_CSV_CREDIT_COLS = ["credit", "deposits", "deposit", "credit amount", "amount credit"]
_CSV_AMOUNT_COLS = ["amount", "transaction amount", "net amount"]

# TD chequing/savings CSV exports have NO header row.
# Columns are always in this fixed order: Date, Description, Debit, Credit, Balance
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
    (first cell of first row is a date, not a column label).
    """
    try:
        with open(file_path, newline="", encoding="utf-8-sig") as f:
            first_line = f.readline().strip()
        if not first_line:
            return False
        first_cell = first_line.split(",")[0].strip().strip('"')
        # If the first cell parses as a date, there's no header row
        return normalise_date(first_cell) is not None
    except Exception:
        return False


def parse_csv(file_path: Path) -> list[dict]:
    """
    Parse a CSV bank statement export into a list of normalised transaction dicts.

    Supports:
      • TD Bank headerless (Date, Description, Debit, Credit, Balance) — no column names
      • TD Bank with headers: Date, Description, Debit, Credit
      • Generic single-amount column with positive = credit, negative = debit
      • Any delimiter (auto-sniffed)

    Personal info is scrubbed from descriptions before returning.
    Returns list of dicts: {date, description, amount, type}
    """
    transactions = []

    try:
        # Sniff delimiter — handles comma, tab, semicolon
        with open(file_path, newline="", encoding="utf-8-sig") as f:
            sample = f.read(4096)
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        sep = dialect.delimiter
    except Exception:
        sep = ","

    # TD headerless format: no column row, fixed 5-column order
    if _is_td_headerless(file_path):
        df = pd.read_csv(file_path, sep=sep, encoding="utf-8-sig",
                         header=None, names=_TD_HEADERLESS_COLS,
                         skip_blank_lines=True, dtype=str)
    else:
        df = pd.read_csv(file_path, sep=sep, encoding="utf-8-sig",
                         skip_blank_lines=True, dtype=str)

    # Drop fully-empty rows
    df.dropna(how="all", inplace=True)

    # Build a lowercase→original map for column lookup
    col_map = {c.lower().strip(): c for c in df.columns}
    cols_lower = list(col_map.keys())

    date_col   = _find_col(cols_lower, _CSV_DATE_COLS)
    desc_col   = _find_col(cols_lower, _CSV_DESC_COLS)
    debit_col  = _find_col(cols_lower, _CSV_DEBIT_COLS)
    credit_col = _find_col(cols_lower, _CSV_CREDIT_COLS)
    amount_col = _find_col(cols_lower, _CSV_AMOUNT_COLS)

    if not date_col or not desc_col:
        console.print(f"[yellow]  Warning: could not detect date/description columns in {file_path.name}. "
                      f"Found columns: {list(df.columns)}[/yellow]")
        return []

    for idx, row in df.iterrows():
        try:
            raw_date = str(row[col_map[date_col]]).strip()
            date = normalise_date(raw_date)
            if not date:
                log.debug("Row %s: unparseable date %r — skipped", idx, raw_date)
                continue

            description = scrub_description(str(row[col_map[desc_col]]).strip())
            if not description or description.lower() in ("nan", ""):
                continue

            # ── Amount resolution ──────────────────────────────────────────────
            amount = None
            txn_type = None

            if debit_col and credit_col:
                # Separate debit / credit columns (TD Bank style)
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
                # Single signed-amount column
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
                "date": date,
                "description": description,
                "amount": round(amount, 2),
                "type": txn_type,
            })

        except Exception as e:
            log.debug("Row %s parse error: %s", idx, e)
            continue

    return transactions


# ── PDF parsing ───────────────────────────────────────────────────────────────

_AMOUNT_RE = re.compile(r"-?\$?[\d,]+\.\d{2}")

# TD PDF date format: "FEB02", "JAN30", "FEB 27" — month abbrev + day, no year
_TD_DATE_RE = re.compile(
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*(\d{1,2})$",
    re.IGNORECASE,
)

# Rows to skip in TD transaction table (after lowercasing + space-removal)
_TD_SKIP_DESCRIPTIONS = {
    "startingbalance",
    "starting balance",
    "closingbalance",
    "closing balance",
    "balanceforward",
    "balance forward",
    "",
}


def _normalise_td_date(raw: str) -> Optional[str]:
    """
    Parse TD PDF date format: 'FEB02' or 'FEB 27' → 'YYYY-MM-DD'.
    Year is inferred: if the resulting date is in the future, roll back one year.
    """
    raw = raw.strip()
    # Insert space: "FEB02" → "FEB 02"
    raw = re.sub(r"([A-Za-z]{3})(\d{1,2})", r"\1 \2", raw)
    return normalise_date(raw)  # existing no-year inference handles the rest


def _is_td_transaction_table(header_row: list[str]) -> bool:
    """
    Return True if this table's header looks like TD's transaction table:
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
      Header row: ['Description', 'Withdrawals', 'Deposits', 'Date', 'Balance']

    Special challenges handled:
      • Multi-transaction cells: pdfplumber merges two rows with '\\n'
        e.g. description='TXN_A\\nTXN_B', withdrawals='10.00\\n20.00', date='FEB01\\nFEB03'
        → split and treat as 2 separate transactions
      • Split-column merge: two transactions merged where one is a debit and the
        next is a credit — wd and dep each have only one value (no \\n) but
        description has two. Yield the debit now; carry the credit to the next
        sub-entry.
      • Skip: STARTINGBALANCE, empty rows, totals row (no date)
      • Deposits column → type='credit', Withdrawals → type='debit'

    Returns (transactions, dropped) where dropped counts rows skipped unexpectedly.
    """
    results = []
    dropped = 0
    rows = [[str(c or "").strip() for c in row] for row in table]

    if not rows:
        return results, dropped

    # Identify column indices from header
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
        # Pad short rows
        while len(row) < len(header):
            row.append("")

        raw_desc = row[desc_idx]
        raw_date = row[date_idx]

        # Skip empty rows and totals row (no date)
        if not raw_desc and not raw_date:
            continue

        # Split multi-transaction cells on \n
        descs = [d.strip() for d in raw_desc.split("\n")]
        dates = [d.strip() for d in raw_date.split("\n")]
        wds   = [d.strip() for d in (row[wd_idx].split("\n")  if wd_idx  is not None else [""])]
        deps  = [d.strip() for d in (row[dep_idx].split("\n") if dep_idx is not None else [""])]
        # Balance column — used only to detect when its value bleeds into wds/deps
        bals  = [d.strip() for d in (row[bal_idx].split("\n") if bal_idx is not None else [""])]

        # Capture pre-pad counts for split-column merge detection below.
        _n_wd_vals  = sum(1 for x in wds  if re.sub(r"[$, ]", "", x))
        _n_dep_vals = sum(1 for x in deps if re.sub(r"[$, ]", "", x))
        _bals_pre_pad = bals.copy()

        # Base n only on descriptions and dates — NOT wds/deps.
        # The last row on each TD page has extra amount values that are
        # running page totals (TD statement artifact), not real sub-entries.
        # Including them in n would create phantom sub-entries.
        n = max(len(descs), len(dates))

        # Page-total bleed: if wd has MORE values than descriptions, the
        # surplus values are the page withdrawal total injected by TD's PDF
        # renderer. When this happens, dep also holds the page deposit total
        # (not a real credit) — discard both surpluses.
        if len(wds) > n:
            wds  = wds[:n]
            deps = [""] * n

        descs = _pad(descs, n)
        dates = _pad(dates, n)
        wds   = _pad(wds,   n)
        deps  = _pad(deps,  n)
        bals  = _pad(bals,  n)

        # ── Split-column merge: reorder amounts to match descriptions ──────────
        # When n=2 with exactly 1 withdrawal AND 1 deposit (no \n in either
        # amount column), pdfplumber merged two opposite-type transactions into
        # one PDF row.  After padding, both amounts land in sub-entry 0, which
        # causes the split_wd_carry path below to assume credit-first — correct
        # when the balance column has 2 values to confirm it, but wrong when
        # only 1 balance value is available (debit was actually first).
        #
        # Strategy:
        #   2 balance values → use delta to determine order:
        #     b[1] - b[0] < 0  →  desc[1] is a debit  →  credit-first
        #     b[1] - b[0] > 0  →  desc[1] is a credit →  debit-first
        #   1 balance value   →  default to debit-first
        #     (desc[0] owns the withdrawal column, desc[1] owns the deposit)
        #
        # After reordering, each sub-entry sees at most one non-zero amount
        # so split_wd_carry is never triggered for these rows.
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
                # desc[0] → deposit (credit), desc[1] → withdrawal (debit)
                wds  = ["", wds[0]]
                deps = [deps[0], ""]
            else:
                # desc[0] → withdrawal (debit), desc[1] → deposit (credit)
                wds  = [wds[0], ""]
                deps = ["", deps[0]]

        # dep_carry: when pdfplumber merges a fee row (e.g. MONTHLYACCOUNTFEE)
        # with its rebate row (ACCTFEEREBATE), the fee summary box at the bottom
        # of the page bleeds into the last merged row.  The pattern we see is:
        #   sub-entry 0 (fee):    wd="X.XX"  dep="X.XX"   ← dep mirrors wd
        #   sub-entry 1 (rebate): wd="garbage" dep="garbage" ← both contaminated
        # The real credit amount appears in dep[0] not dep[1].
        # dep_carry saves it from entry 0 and applies it to the contaminated entry 1.
        dep_carry: Optional[str] = None

        # split_wd_carry: when two transactions (one credit, one debit) are merged
        # into a single pdfplumber cell, the amount columns each have only ONE value
        # (no \n) but description has two.  We yield the credit immediately and carry
        # the withdrawal amount to the next sub-entry.
        # NOTE: TD bank statements consistently show the credit entry first in merged
        # pairs (e.g. TD CHQ Offer MSP credit on JAN14 merged with Amazon debit JAN15).
        split_wd_carry: Optional[str] = None

        for desc, raw_d, wd, dep, bal in zip(descs, dates, wds, deps, bals):
            # Skip non-transaction rows (opening/closing balance labels, blanks)
            if desc.lower().replace(" ", "") in _TD_SKIP_DESCRIPTIONS:
                continue
            if not raw_d:
                continue  # totals / summary row with no date

            date = _normalise_td_date(raw_d)
            if not date:
                continue

            # Determine amount and type
            amount = None
            txn_type = None

            wd_clean  = re.sub(r"[$, ]", "", wd)
            dep_clean = re.sub(r"[$, ]", "", dep)
            bal_clean = re.sub(r"[$, ]", "", bal)

            # Guard 1: balance-column bleed — wd equals the running balance value
            if wd_clean and wd_clean == bal_clean:
                wd_clean = ""

            # Parse as floats for Guard 2 — avoids "0.00" being truthy as a string
            # while being a useless amount (page deposit total = $0 bleeds into dep).
            try:
                wd_val = float(wd_clean) if wd_clean else 0.0
            except ValueError:
                wd_val = 0.0
            try:
                dep_val = float(dep_clean) if dep_clean else 0.0
            except ValueError:
                dep_val = 0.0

            # Apply split-column carry: the previous sub-entry was the credit half of
            # a merged pair; this sub-entry is the debit half.
            if split_wd_carry is not None:
                if not wd_clean and not dep_clean:
                    wd_clean = split_wd_carry
                    wd_val   = float(wd_clean)
                split_wd_carry = None

            # Guard 2: fee-summary contamination in merged rows
            # A real transaction should appear in ONLY one of wd or dep.
            # Both non-zero (by float value) signals bleed-in from adjacent content.
            if wd_val > 0 and dep_val > 0:
                if wd_clean == dep_clean:
                    # Equal values: this is the fee row (e.g. MONTHLYACCOUNTFEE).
                    # dep value is really the rebate that belongs to the NEXT entry.
                    dep_carry = dep_clean   # save for next sub-entry
                    dep_clean = ""          # suppress duplicate credit here
                elif dep_carry is not None:
                    # Both columns have different values (fee-summary garbage).
                    # Use the carried deposit from the previous sub-entry as credit.
                    wd_clean  = ""
                    dep_clean = dep_carry
                    dep_carry = None
                else:
                    # Split-column merge: pdfplumber packed a credit and a debit into
                    # the same cell (each amount column has only one value, no \n).
                    # Yield the credit now and carry the withdrawal to the next sub-entry.
                    split_wd_carry = wd_clean
                    wd_clean = ""
            else:
                # If this sub-entry has no amounts of its own but dep_carry is set,
                # apply the carry now (e.g. CANCELE-TFR follows SENDE-TFR with equal
                # wd==dep; the cancel credit belongs to this empty sub-entry).
                if dep_carry is not None and not wd_clean and not dep_clean:
                    dep_clean = dep_carry
                    dep_val   = float(dep_carry)
                dep_carry = None   # clear after applying (or when not needed)

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
                # Row has a date and description but no usable amount — warn if
                # it looks like a real transaction (not a filler/blank row).
                if desc and desc.lower().replace(" ", "") not in _TD_SKIP_DESCRIPTIONS:
                    dropped += 1
                    console.print(
                        f"  [yellow]⚠ drop:[/yellow] no amount found for "
                        f"[dim]{desc[:50]!r}[/dim] on {date}"
                    )
                continue

            results.append({
                "date": date,
                "description": scrub_description(desc),
                "amount": amount,
                "type": txn_type,
            })

    return results, dropped


# Generic date regex (for non-TD PDFs)
_DATE_RE = re.compile(
    r"\b(?:\d{1,2}[-/]\d{1,2}(?:[-/]\d{2,4})?|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:,?\s+\d{4})?|"
    r"\d{4}[-/]\d{2}[-/]\d{2})\b",
    re.IGNORECASE,
)


def _extract_from_text(text: str) -> list[dict]:
    """Fallback: scan raw page text for date + amount patterns (non-TD PDFs)."""
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
        start = date_m.end()
        end   = amount_m.start()
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
            "date": date,
            "description": scrub_description(description),
            "amount": round(abs(val), 2),
            "type": "debit",
        })
    return results


# ── Credit card (CC) text parser ─────────────────────────────────────────────
# TD Visa-style CC statements have NO pdfplumber tables.
# Transactions appear as raw text lines:
#   MMMDD MMMDD MERCHANT-NAME $X.XX
#   MMMDD MMMDD PREAUTHORIZEDPAYMENT -$X.XX   ← negative = payment/credit
#
# First date  = transaction date (what we store)
# Second date = posting date (ignored)
# Negative amount = credit (payment or refund); positive = debit (purchase/charge)
#
# Raw text quirks from pdfplumber:
#  • Page-1 lines may have extra side-panel text after the amount — stopped at
#    the first matched $X.XX so the trailing text is ignored.
#  • Multi-line merchant names (description wraps to next line) — we capture the
#    first line only; the orphaned continuation line is silently dropped.

_TD_VISA_TXN_RE = re.compile(
    r"^((?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s*\d{1,2})"   # trans date MMM[space]DD
    r"\s+"
    r"(?:(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s*\d{1,2})"   # posting date (ignored)
    r"\s+"
    r"(.+?)"                                                                    # description (non-greedy)
    r"\s+"
    r"(-?\$[\d,]+\.\d{2})",                                                    # amount (- prefix = credit)
    re.IGNORECASE | re.MULTILINE,
)

# Lines to always skip — account summaries, headers, totals, boilerplate
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
    r"PAYMENTS?\s*[&and]+\s*CREDITS?|"        # balance summary rows
    r"PURCHASES?\s*[&and]+\s*OTHER\s*CHARGES?|"
    r"CASH\s*ADVANCES?|"
    r"SUB-?TOTAL|"
    r"INTEREST\s*(CHARGED|RATE|FREE|\$)|"     # "Interest $0.00" summary
    r"ANNUAL\s+FEE|"
    r"PREVIOUS\s*(?:STATEMENT\s*)?BALANCE|"   # "Previous Balance $0.00" in CC summary
    r"^\s*FEES?\s|"                           # "Fees $0.00" standalone line in CC summary
    r"TD\s+CANADA\s+TRUST|"                  # payment stub line
    r"^\s*\d{1,4}\s*$",                      # bare page numbers / single numbers
    re.IGNORECASE | re.MULTILINE,
)


def _is_td_visa_text(text: str) -> bool:
    """
    Return True if the text block contains the double-MMMDD date pattern
    characteristic of TD Visa CC statement transactions.

    Uses MULTILINE so ^ matches start of each line in the block.
    """
    return bool(_TD_VISA_TXN_RE.search(text))


_SUSPECT_CC_AMOUNT_RE = re.compile(r"-?\$[\d,]+\.\d{2}")


def _parse_td_visa_text(text: str) -> tuple[list[dict], int]:
    """
    Parse TD Visa CC statement raw text (no tables).

    Each transaction line:
        MMMDD MMMDD DESCRIPTION $AMOUNT
        MMMDD MMMDD PAYMENT      -$AMOUNT   ← negative amount = credit

    Negative amounts → type='credit' (payment/refund reducing balance).
    Positive amounts → type='debit'  (purchase/charge).

    Returns (transactions, dropped) where dropped counts lines that contained a
    dollar amount but did not match the transaction pattern — possible extraction
    artefact worth investigating.
    """
    results = []
    dropped = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        # Transaction regex takes priority — if a line starts with two dates
        # it's a transaction even if it also contains side-panel summary text
        # (e.g. "JAN27 JAN28 SOME MERCHANT $9.99 Payment Due Date Mar.20").
        # The skip regex is only applied to lines that don't look like transactions.
        m = _TD_VISA_TXN_RE.match(line)
        if not m:
            # Warn if the line has a dollar amount but no matching date prefix —
            # this can happen when x_tolerance=1 spaces out date characters.
            if _SUSPECT_CC_AMOUNT_RE.search(line) and not _TD_VISA_SKIP_RE.search(line):
                dropped += 1
                console.print(
                    f"  [yellow]⚠ drop:[/yellow] line has amount but no date match "
                    f"[dim]{line[:70]!r}[/dim]"
                )
            continue

        raw_date_str = m.group(1)
        description  = m.group(2).strip()
        raw_amount   = m.group(3)   # may have leading '-'

        # Parse date: "JAN27" → "YYYY-MM-DD"
        date = _normalise_td_date(raw_date_str)
        if not date:
            continue

        # Parse amount — negative means credit (payment/refund)
        try:
            val = float(re.sub(r"[$,]", "", raw_amount))
        except ValueError:
            continue

        if val == 0 or not description:
            continue

        amount   = round(abs(val), 2)
        txn_type = "credit" if val < 0 else "debit"

        results.append({
            "date": date,
            "description": scrub_description(description),
            "amount": amount,
            "type": txn_type,
        })

    # Warn if no transactions were found on a page that looked like CC format.
    # This typically means the text extraction changed (pdfplumber update, new PDF
    # layout) and the regex no longer matches — inspect the file to diagnose.
    if not results and not dropped:
        has_any_amount = bool(_SUSPECT_CC_AMOUNT_RE.search(text))
        if has_any_amount:
            console.print(
                "  [yellow]⚠ warning:[/yellow] CC page matched no transactions "
                "but contains dollar amounts — statement format may have changed"
            )

    return results, dropped


def _extract_text_spaced(page) -> str:
    """
    Extract page text with preserved word spacing — for CC statements only.

    pdfplumber's default extract_text(x_tolerance=3) collapses gaps smaller
    than 3px, causing "WAVES COFFEE CITY POINT SURREY" to become
    "WAVESCOFFEECITYPOINTSURREY" in CC statements where merchant names are
    stored as individually-positioned characters.

    x_tolerance=1: a gap of just 1px between characters triggers a space,
    restoring the visible word boundaries from the PDF.

    y_tolerance=5: characters within 5px vertically are treated as the same
    line — needed because page-1 of CC statements has dates and their
    corresponding description text at slightly different y-positions (~0.8px
    apart) due to the two-column layout. Line-to-line spacing is ~19px so
    this never accidentally merges two separate transaction rows.

    WARNING: only call this when you've confirmed the page is CC-style.
    x_tolerance=1 can produce garbled output on PDFs from other banks where
    characters are stored with tight kerning and appear spaced only visually.
    The generic fallback uses the default extract_text() to stay safe.
    """
    return page.extract_text(x_tolerance=1, y_tolerance=5) or ""


def parse_pdf(file_path: Path) -> list[dict]:
    """
    Extract transactions from a PDF bank statement using pdfplumber.

    Strategy (per page):
      1. For each table: if it looks like a TD transaction table → _parse_td_table()
      2. Otherwise skip non-transaction tables (account info, fee summaries)
      3. If no tables → reconstruct text via word bounding boxes (preserves spaces)
         a. Looks like CC statement → _parse_td_visa_text()
         b. Otherwise → generic _extract_from_text() fallback

    Returns (transactions, dropped) where dropped counts rows that were skipped
    unexpectedly and printed a warning during parsing.
    """
    transactions = []
    total_dropped = 0

    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                found_td_txn_table = False

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
                        # All other tables (account info, fees, etc.) are silently ignored
                        # — they contain no transaction data we need

                # Fall through to text parsing if no TD transaction table was found.
                # This handles CC statements (no tables at all) AND the edge case where
                # pdfplumber detects non-transaction boxes as tables on a CC page.
                if not found_td_txn_table:
                    # First pass: standard extraction to detect statement type safely
                    default_text = page.extract_text() or ""
                    if _is_td_visa_text(default_text):
                        # CC statement confirmed — re-extract with tighter x_tolerance
                        # to restore spaces in merchant names.
                        # x_tolerance=1 is only safe once we know it's CC format;
                        # it can garble other banks' PDFs with tight character kerning.
                        spaced_text = _extract_text_spaced(page)
                        rows, d = _parse_td_visa_text(spaced_text)
                        transactions.extend(rows)
                        total_dropped += d
                    elif not tables:
                        # Generic fallback only when there are truly no tables.
                        # If tables exist but are all non-transaction (e.g., account-info
                        # tables on a chequing page with no transactions), skip the
                        # fallback to avoid pulling in garbage from fee summaries.
                        transactions.extend(_extract_from_text(default_text))

    except Exception as e:
        console.print(f"[red]  PDF parse error for {file_path.name}: {e}[/red]")

    if not transactions:
        console.print(
            f"  [yellow]⚠ warning:[/yellow] no transactions extracted from "
            f"[dim]{file_path.name}[/dim] — run [cyan]--inspect[/cyan] to diagnose"
        )

    return transactions, total_dropped


# ── Account detection ────────────────────────────────────────────────────────

# Maps keywords found in filenames to a canonical account label.
# Add more entries here as you add new banks/accounts.
_ACCOUNT_KEYWORDS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"visa",              re.IGNORECASE), "creditcard"),
    (re.compile(r"mastercard|mc",     re.IGNORECASE), "creditcard"),
    (re.compile(r"chequ|checking",    re.IGNORECASE), "chequing"),
    (re.compile(r"saving",            re.IGNORECASE), "savings"),
    (re.compile(r"line.of.credit|loc",re.IGNORECASE), "loc"),
]


def detect_account(filename: str) -> str:
    """
    Infer the account label from a statement filename.

    Examples:
      'MY_CHEQUING_ACCOUNT_...'  → 'chequing'
      'MY_VISA_CARD_...'         → 'creditcard'

    Returns 'unknown' if no pattern matches — you can update it manually
    in the DB or add a rule above.
    """
    for pattern, label in _ACCOUNT_KEYWORDS:
        if pattern.search(filename):
            return label
    return "unknown"


# ── Source filename sanitisation ─────────────────────────────────────────────

# Patterns that look like account/card numbers embedded in filenames
_ACCT_NUM_RE = re.compile(r"\d{4}[-_]\d{4,10}|\d{8,}")


def sanitize_source_filename(filename: str) -> str:
    """
    Strip account/card number patterns from a filename before storing in the DB.

    Removes segments matching NNNN-NNNNNNN or 8+ consecutive digits.
    Example:
      'CHEQUING_ACCOUNT_XXXX-XXXXXXX_Jan_30-Feb_27_2026.pdf'
      → 'CHEQUING_ACCOUNT_Jan_30-Feb_27_2026.pdf'
    """
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    # Remove segments that look like account numbers
    cleaned = _ACCT_NUM_RE.sub("", stem)
    # Collapse multiple underscores/hyphens left behind
    cleaned = re.sub(r"[_\-]{2,}", "_", cleaned).strip("_-")
    return cleaned + suffix


# ── Pre-categorization rules ─────────────────────────────────────────────────
# Applied at insert time for descriptions we can identify with certainty,
# before the AI categorizer runs in Phase 3.
# These are NOT overwritten by the AI (confirmed=1 protects them).
# Add rows here as you discover recurring patterns in your statements.

_PRECATEGORY_RULES: list[tuple[re.Pattern, str, int]] = [
    # Each entry: (pattern, category, confirmed)
    # confirmed=1 → AI will NOT overwrite this in Phase 3 (use for certainties)
    # confirmed=0 → AI/human review CAN correct this (use for educated guesses)

    # ── Transfers / payments ──────────────────────────────────────────────────
    # TD Visa pre-authorized payment (Visa bill paid from chequing) — certain
    (re.compile(r"TDVISAPREAUTHPYMT|TD\s*VISA\s*PREAUTH", re.IGNORECASE), "transfer", 1),
    # TD Visa CC statement: pre-authorized payment received on the card — certain
    (re.compile(r"PREAUTHORIZED\s*PAYMENT|PAYMENT\s*[-–]?\s*THANK\s+YOU", re.IGNORECASE), "transfer", 1),
    # TD Line of Credit payment — certain
    (re.compile(r"TD\s*LOC\s*PYMT|TDLOC", re.IGNORECASE), "transfer", 1),
    # Outgoing e-Transfer (SENDE-TFR, SENDE-TFR***xyz) — confirmed=0 because
    # some are rent/bills paid to landlord via e-transfer, not just internal
    # account moves.  The AI categorizer (Phase 3) and human review can correct
    # individual entries to 'bills_utilities' or other categories.
    (re.compile(r"SENDE?-?TFR|SEND\s*TRANSFER", re.IGNORECASE), "transfer", 0),
    # Cancelled/reversed e-Transfer — always a transfer reversal, certain
    (re.compile(r"CANCELE?-?TFR|CANCEL\s*TRANSFER", re.IGNORECASE), "transfer", 1),
    # Incoming e-transfers / deposits from another account — certain
    (re.compile(r"RECV\s*TFR|RECEIVE\s*TRANSFER|INTERNET\s*TRANSFER", re.IGNORECASE), "transfer", 1),

    # ── Income ────────────────────────────────────────────────────────────────
    (re.compile(r"PAYROLL|DIRECT\s*DEP|DIRECT\s*DEPOSIT", re.IGNORECASE), "income", 1),

    # ── Bank fees (then rebated) ───────────────────────────────────────────────
    (re.compile(r"MONTHLYACCOUNTFEE|MONTHLY\s*ACCOUNT\s*FEE", re.IGNORECASE), "fees", 1),
    (re.compile(r"ACCTFEEREBATE|ACCOUNT\s*FEE\s*REBATE", re.IGNORECASE), "fees", 1),
]


def precategorize(description: str) -> tuple[str, int]:
    """
    Check description against known patterns.

    Returns (category, confirmed) where confirmed=1 means the AI won't
    overwrite this category in Phase 3, confirmed=0 means it's an educated
    guess that AI/human review can still correct.
    Returns ('unknown', 0) if no rule matches.
    """
    for pattern, category, confirmed in _PRECATEGORY_RULES:
        if pattern.search(description):
            return category, confirmed
    return "unknown", 0


# ── DB insertion ─────────────────────────────────────────────────────────────

def _load_corrections_for_parser() -> dict:
    """
    Load corrections.json for use at import time.
    Returns an empty dict if the file is missing or malformed.
    Keys are pre-uppercased; meta keys (_comment etc.) are stripped.
    """
    try:
        raw = json.loads(CORRECTIONS_FILE.read_text(encoding="utf-8"))
        return {k.upper(): v for k, v in raw.items() if not k.startswith("_")}
    except Exception:
        return {}


def _corrections_category(description: str, corrections: dict) -> Optional[tuple[str, int]]:
    """
    Return (category, confirmed=1) if any corrections key is a substring of
    description (case-insensitive), else None.

    Matches the same logic as the AI categorizer so that corrections applied
    at import time and at categorize time are identical.
    """
    desc_upper = description.upper()
    for key, override in corrections.items():
        if key in desc_upper:
            return override.get("category", "other"), 1
    return None


def insert_transactions(
    conn: sqlite3.Connection,
    rows: list[dict],
    source_file: str,
    account: str = "unknown",
) -> dict:
    """
    Insert normalised transaction rows into the DB.

    Category resolution order (first match wins):
      1. corrections.json  — user-defined rules, confirmed=1, no AI needed
      2. precategorize()   — hardcoded regex rules for common patterns
      3. 'unknown'         — AI categorizer will fill this in later

    Skips rows whose hash already exists (dedup).
    Returns counts: inserted, skipped, failed.
    """
    inserted = skipped = failed = 0
    corrections = _load_corrections_for_parser()

    # Track how many times each (date, description, amount) combo has appeared
    # in this batch so that two legitimately identical transactions in the same
    # file (e.g. two same-amount charges from the same merchant on the same day)
    # get distinct hashes and are both inserted.
    occurrence_counter: dict[tuple, int] = {}

    for row in rows:
        try:
            key = (row["date"], row["description"], row["amount"])
            occurrence = occurrence_counter.get(key, 0)
            occurrence_counter[key] = occurrence + 1
            h = compute_hash(row["date"], row["description"], row["amount"], occurrence)

            exists = conn.execute(
                "SELECT 1 FROM transactions WHERE hash = ?", (h,)
            ).fetchone()

            if exists:
                skipped += 1
                continue

            # 1. corrections.json wins — instant, no LLM needed
            corr = _corrections_category(row["description"], corrections)
            if corr:
                category, confirmed = corr
            else:
                # 2. hardcoded precategory rules
                category, confirmed = precategorize(row["description"])

            conn.execute(
                """
                INSERT INTO transactions
                  (date, description, amount, type, account, category, confirmed, source_file, hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (row["date"], row["description"], row["amount"],
                 row["type"], account, category, confirmed, source_file, h)
            )
            inserted += 1

        except Exception as e:
            log.debug("Insert failed for row %s: %s", row, e)
            failed += 1

    conn.commit()
    return {"inserted": inserted, "skipped": skipped, "failed": failed}


# ── Statement balance verification ───────────────────────────────────────────
#
# After parsing, we extract the summary totals printed on the statement itself
# and compare against the transactions in the DB.  A mismatch means the parser
# missed (or double-counted) rows — useful to catch early rather than in Phase 3.
#
# For CC statements  : "Purchases & Other Charges $X.XX" and "Payments & Credits $X.XX"
# For chequing PDFs  : "Starting Balance" and ending balance from the Balance column

# CC statement — "Purchases & Other Charges $X.XX"
_CC_CHARGES_RE  = re.compile(r"Purchases?\s*[&and]+\s*Other\s*Charges?\s+\$?([\d,]+\.\d{2})", re.IGNORECASE)
_CC_PAYMENTS_RE = re.compile(r"Payments?\s*[&and]+\s*Credits?\s+\$?([\d,]+\.\d{2})", re.IGNORECASE)
_CC_NEW_BAL_RE  = re.compile(r"NEW\s*BALANCE\s+\$?([\d,]+\.\d{2})", re.IGNORECASE)

# Chequing PDF — opening balance row is labelled "STARTINGBALANCE"; closing
# balance is the last non-empty value in the Balance column across all pages.
_CHQ_START_BAL_RE  = re.compile(r"STARTINGBALANCE|STARTING\s+BALANCE", re.IGNORECASE)
_DOLLAR_RE         = re.compile(r"\$?([\d,]+\.\d{2})")


def _parse_dollar(s: str) -> Optional[float]:
    """Parse '$1,234.56' or '1234.56' → float, or None on failure."""
    s = re.sub(r"[$, ]", "", (s or "").strip())
    try:
        return float(s) if s else None
    except ValueError:
        return None


def verify_statement(file_path: Path, account: str) -> dict:
    """
    Open a statement PDF and extract the official summary totals for reconciliation.

    Returns a dict:
      {
        "account":          str,
        "file":             str,
        # CC fields
        "expected_charges": float | None,   # Purchases & Other Charges
        "expected_payments":float | None,   # Payments & Credits
        "expected_new_bal": float | None,   # New Balance
        # chequing fields
        "opening_balance":  float | None,
        "closing_balance":  float | None,
      }
    None means the value couldn't be found in the statement.
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
            full_text = "\n".join(
                (page.extract_text() or "") for page in pdf.pages
            )

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
                # Find opening / closing balance from the transaction table.
                #
                # TD chequing statements don't have a labelled "CLOSING BALANCE" row —
                # the balance just stops after the last transaction.  We extract:
                #   opening  → the balance on the STARTINGBALANCE row (first page)
                #   closing  → the last non-empty balance value across all pages
                #              (which is the running balance after the final transaction)
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
                            cells = [str(c or "").strip() for c in row]
                            desc = cells[0] if cells else ""
                            bal_cell = cells[bal_idx] if bal_idx < len(cells) else ""

                            # Split multi-transaction rows on \n
                            descs = [d.strip() for d in desc.split("\n")]
                            bals  = [b.strip() for b in bal_cell.split("\n")]

                            for d, b in zip(descs, bals):
                                if _CHQ_START_BAL_RE.search(d) and result["opening_balance"] is None:
                                    result["opening_balance"] = _parse_dollar(
                                        re.sub(r"[$,]", "", b)
                                    )
                                # Track every non-empty balance value; the last one
                                # seen across all pages becomes the closing balance.
                                b_val = _parse_dollar(re.sub(r"[$,]", "", b))
                                if b_val is not None:
                                    result["closing_balance"] = b_val
    except Exception as e:
        log.debug("verify_statement failed for %s: %s", file_path.name, e)

    return result


# ── Post-parse sanity checks ─────────────────────────────────────────────────

def _check_outliers(rows: list[dict]) -> list[dict]:
    """
    Flag transactions whose amount is suspiciously large relative to the rest
    of the file.  This catches parser bugs where two amounts are merged into
    one (e.g. a $62.99 withdrawal absorbing a $45,000 deposit due to a
    split-column merge mis-detection).

    Strategy:
      • Collect all debit amounts.
      • Compute median.  If median > 0, flag any debit > 10× median.
      • Also unconditionally flag any single transaction ≥ $10,000 as a
        "large transaction" note (not necessarily wrong, but worth a glance).

    Returns a list of warning dicts:
      {"description": str, "amount": float, "date": str, "reason": str}
    """
    warnings = []
    debits = [r["amount"] for r in rows if r.get("type") == "debit" and r["amount"] > 0]
    if not debits:
        return warnings

    sorted_debits = sorted(debits)
    mid = len(sorted_debits) // 2
    median = (
        sorted_debits[mid]
        if len(sorted_debits) % 2 == 1
        else (sorted_debits[mid - 1] + sorted_debits[mid]) / 2
    )
    outlier_threshold = max(median * 10, 5_000)

    for r in rows:
        if r.get("type") != "debit":
            continue
        if r["amount"] >= outlier_threshold:
            reason = (
                f"${r['amount']:,.2f} is ≥ 10× the median debit "
                f"(${median:,.2f}) — possible merge artifact"
                if r["amount"] >= median * 10
                else f"${r['amount']:,.2f} is a large transaction (≥ $5,000)"
            )
            warnings.append({
                "description": r["description"],
                "amount":      r["amount"],
                "date":        r["date"],
                "reason":      reason,
            })

    return warnings


# ── Main entry point ─────────────────────────────────────────────────────────

def parse_new_statements(
    statements_dir: Path = STATEMENTS_DIR,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """
    Scan statements_dir for .pdf and .csv files not yet imported.
    Parse each one and insert into the DB.

    Returns a list of summary dicts, one per file:
      {file, parsed, inserted, skipped, failed}
    """
    conn = sqlite3.connect(db_path)
    results = []

    files = sorted(
        list(statements_dir.glob("*.pdf")) + list(statements_dir.glob("*.csv"))
    )

    if not files:
        console.print(f"[dim]No statement files found in {statements_dir}[/dim]")
        conn.close()
        return []

    for file_path in files:
        filename       = file_path.name
        safe_filename  = sanitize_source_filename(filename)

        # Use the sanitized name for the DB skip check AND storage
        if is_already_imported(conn, safe_filename):
            console.print(f"[dim]  Skipping (already imported): {filename}[/dim]")
            results.append({
                "file": filename,
                "parsed": 0, "inserted": 0, "skipped": 0, "failed": 0,
                "status": "already imported",
            })
            continue

        account = detect_account(filename)
        console.print(f"[cyan]  Parsing:[/cyan] {filename}  [dim]→ account: {account}[/dim]")

        suffix = file_path.suffix.lower()
        if suffix == ".csv":
            rows = parse_csv(file_path)
            parse_dropped = 0
        elif suffix == ".pdf":
            rows, parse_dropped = parse_pdf(file_path)
        else:
            continue

        # ── Outlier check ───────────────────────────────────────────────
        outlier_warnings = _check_outliers(rows)
        if outlier_warnings:
            console.print(
                f"  [bold yellow]⚠ {len(outlier_warnings)} outlier amount(s) — "
                f"possible merge artifact:[/bold yellow]"
            )
            for w in outlier_warnings:
                console.print(
                    f"    [yellow]•[/yellow] {w['date']}  "
                    f"[white]{w['description'][:45]}[/white]  "
                    f"[red]${w['amount']:,.2f}[/red]  [dim]{w['reason']}[/dim]"
                )

        counts = insert_transactions(conn, rows, safe_filename, account)

        # ── Balance reconciliation (chequing + creditcard) ───────────────
        reconciliation = None
        if suffix == ".pdf":
            rec = verify_statement(file_path, account)
            if account == "chequing" and rec["opening_balance"] is not None:
                # Net = sum(credits) - sum(debits) should equal closing - opening
                net_parsed = sum(
                    r["amount"] * (1 if r["type"] == "credit" else -1) for r in rows
                )
                expected_net = (rec["closing_balance"] or 0) - rec["opening_balance"]
                delta = round(abs(net_parsed - expected_net), 2)
                ok = delta < 0.05
                mark = "[green]✓[/green]" if ok else "[bold red]✗[/bold red]"
                console.print(
                    f"  {mark} Balance check:  "
                    f"parsed net [cyan]${net_parsed:+,.2f}[/cyan]  "
                    f"statement net [cyan]${expected_net:+,.2f}[/cyan]"
                    + ("" if ok else f"  [red]Δ ${delta:,.2f} — investigate[/red]")
                )
                reconciliation = {
                    "opening":     rec["opening_balance"],
                    "closing":     rec["closing_balance"],
                    "parsed_net":  round(net_parsed, 2),
                    "expected_net": round(expected_net, 2),
                    "delta":       delta,
                    "ok":          ok,
                }
            elif account == "creditcard" and rec["expected_charges"] is not None:
                total_debits  = sum(r["amount"] for r in rows if r["type"] == "debit")
                total_credits = sum(r["amount"] for r in rows if r["type"] == "credit")
                delta_charges  = round(abs(total_debits  - rec["expected_charges"]),  2)
                delta_payments = round(abs(total_credits - (rec["expected_payments"] or 0)), 2)
                ok = delta_charges < 0.05 and delta_payments < 0.05
                mark = "[green]✓[/green]" if ok else "[bold red]✗[/bold red]"
                console.print(
                    f"  {mark} CC check:  "
                    f"charges [cyan]${total_debits:,.2f}[/cyan] / [dim]expected ${rec['expected_charges']:,.2f}[/dim]  "
                    f"payments [cyan]${total_credits:,.2f}[/cyan] / [dim]expected ${rec['expected_payments'] or 0:,.2f}[/dim]"
                    + ("" if ok else f"  [red]mismatch — investigate[/red]")
                )
                reconciliation = {
                    "expected_charges":  rec["expected_charges"],
                    "expected_payments": rec["expected_payments"],
                    "parsed_charges":    round(total_debits, 2),
                    "parsed_payments":   round(total_credits, 2),
                    "delta_charges":     delta_charges,
                    "delta_payments":    delta_payments,
                    "ok":                ok,
                }

        results.append({
            "file": safe_filename,
            "parsed":   len(rows),
            "inserted": counts["inserted"],
            "skipped":  counts["skipped"],
            "failed":   counts["failed"],
            "dropped":  parse_dropped,
            "status": "imported",
            "outlier_warnings": outlier_warnings,
            "reconciliation":   reconciliation,
        })

    conn.close()
    return results


# ── --test mode ───────────────────────────────────────────────────────────────

def run_test():
    """
    Create a temp CSV with 5 known transactions, parse it, print results.
    Run a second time to confirm dedup (0 inserted).
    """
    import tempfile
    import os

    console.rule("[bold cyan]Parser self-test[/bold cyan]")

    test_csv_content = """Date,Description,Debit,Credit
2024-03-01,TIM HORTONS #1234,4.75,
2024-03-03,LOBLAWS,87.42,
2024-03-05,PAYROLL DEPOSIT,,2450.00
2024-03-07,NETFLIX,18.99,
2024-03-10,ESSO GAS STATION,65.00,
"""

    with tempfile.TemporaryDirectory() as tmpdir:
        # Write test CSV
        csv_path = Path(tmpdir) / "test_statement.csv"
        csv_path.write_text(test_csv_content)

        # Use a temp DB
        db_path = Path(tmpdir) / "test.db"

        # Init the schema
        schema_path = Path(__file__).parent.parent / "db" / "schema.sql"
        conn = sqlite3.connect(db_path)
        conn.executescript(schema_path.read_text())
        conn.close()

        # First pass — should insert 5
        console.print("\n[bold]Pass 1 — expect 5 inserted:[/bold]")
        r1 = parse_new_statements(Path(tmpdir), db_path)
        _print_results_table(r1)

        # Second pass — should insert 0 (file-level skip)
        console.print("\n[bold]Pass 2 — expect 0 inserted (already imported):[/bold]")
        r2 = parse_new_statements(Path(tmpdir), db_path)
        _print_results_table(r2)

        # Verify DB
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        conn.close()

        console.print(f"\n[green]DB has {count} transaction(s) — expected 5[/green]")
        if count == 5:
            console.print("[bold green]✓ CSV self-test passed[/bold green]")
        else:
            console.print("[bold red]✗ CSV self-test failed[/bold red]")
            sys.exit(1)

    # ── Visa PDF smoke test ────────────────────────────────────────────────────
    # Looks for any TD Visa PDF in data/statements/.  The actual statement files
    # are not committed to the repo (personal financial data), so this test is
    # skipped on a clean clone.  When the PDF is present it verifies the parser
    # produces at least one transaction with zero dropped rows.
    console.rule("[bold cyan]Visa PDF smoke test[/bold cyan]")
    visa_pdf = next(STATEMENTS_DIR.glob("TD_CASH_BACK_VISA*.pdf"), None)
    if visa_pdf is None:
        console.print("[yellow]  Skipped: no TD Visa PDF found in data/statements/[/yellow]")
    else:
        rows, dropped = parse_pdf(visa_pdf)
        if dropped != 0:
            console.print(f"[bold red]✗ Visa PDF smoke test failed — {dropped} row(s) dropped[/bold red]")
            sys.exit(1)
        if not rows:
            console.print("[bold red]✗ Visa PDF smoke test failed — 0 transactions parsed[/bold red]")
            sys.exit(1)
        console.print(
            f"[bold green]✓ Visa PDF smoke test passed[/bold green] — "
            f"{len(rows)} transaction(s) parsed from {visa_pdf.name}, 0 dropped"
        )


def _print_results_table(results: list[dict]) -> None:
    """Print a rich table summarising parse results."""
    table = Table(
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
    )
    table.add_column("File",     style="white",   min_width=24)
    table.add_column("Parsed",   justify="right")
    table.add_column("Inserted", justify="right", style="bold green")
    table.add_column("Skipped",  justify="right", style="yellow")
    table.add_column("Failed",   justify="right", style="red")
    table.add_column("Dropped",  justify="right")
    table.add_column("Status",   style="dim")

    for r in results:
        dropped = r.get("dropped", 0)
        dropped_str = (
            f"[bold red]{dropped}[/bold red]" if dropped > 0 else "[dim]0[/dim]"
        )
        table.add_row(
            r["file"],
            str(r["parsed"]),
            str(r["inserted"]),
            str(r["skipped"]),
            str(r["failed"]),
            dropped_str,
            r.get("status", ""),
        )

    console.print(table)


def inspect_pdf(file_path: Path) -> None:
    """
    Debug tool: print exactly what pdfplumber extracts from a PDF
    (tables and raw text per page) so the parser logic can be tuned.
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


def reimport_file(file_path: Path, db_path: Path = DB_PATH) -> None:
    """
    Delete all transactions for a previously-imported file and re-parse it.

    Useful after fixing a parser bug — clears the old (wrong) rows so the
    corrected parser can insert fresh ones.

    Usage:
        python src/parser.py --reimport data/statements/MY_CHEQUING.pdf
    """
    filename = sanitize_source_filename(file_path.name)
    conn = sqlite3.connect(db_path)

    existing = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE source_file = ?", (filename,)
    ).fetchone()[0]

    if existing == 0:
        console.print(f"[yellow]No transactions found for '{filename}' — nothing to delete.[/yellow]")
        console.print("[dim]Running fresh import...[/dim]")
    else:
        conn.execute("DELETE FROM transactions WHERE source_file = ?", (filename,))
        conn.commit()
        console.print(f"[red]Deleted {existing} existing transaction(s) for '{filename}'[/red]")

    conn.close()

    account = detect_account(file_path.name)
    console.print(f"[cyan]Re-parsing:[/cyan] {file_path.name}  [dim]→ account: {account}[/dim]")

    suffix = file_path.suffix.lower()
    conn = sqlite3.connect(db_path)
    if suffix == ".csv":
        rows = parse_csv(file_path)
        dropped = 0
    elif suffix == ".pdf":
        rows, dropped = parse_pdf(file_path)
    else:
        console.print(f"[red]Unsupported file type: {suffix}[/red]")
        conn.close()
        return

    counts = insert_transactions(conn, rows, filename, account)
    conn.close()

    console.print(
        f"[green]Re-import done:[/green] "
        f"{counts['inserted']} inserted, {counts['skipped']} skipped, "
        f"{counts['failed']} failed, {dropped} dropped"
    )


if __name__ == "__main__":
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument("--test",     action="store_true", help="Run self-test with fake CSV data")
    arg_parser.add_argument("--inspect",  metavar="FILE",      help="Debug: dump raw pdfplumber output for a PDF")
    arg_parser.add_argument("--reimport", metavar="FILE",      help="Delete existing rows for a file and re-parse it")
    args = arg_parser.parse_args()

    if args.test:
        run_test()
    elif args.inspect:
        inspect_pdf(Path(args.inspect))
    elif args.reimport:
        reimport_file(Path(args.reimport))
    else:
        results = parse_new_statements()
        _print_results_table(results)
        total = sum(r["inserted"] for r in results)
        console.print(f"\n[bold green]{total} new transaction(s) added.[/bold green]")

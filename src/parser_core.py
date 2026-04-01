"""
src/parser_core.py — Bank-agnostic parser utilities.

This module is bank-independent.  It contains:
  • Date normalisation (generic)
  • Transaction dedup / hashing
  • Description scrubbing (PII removal)
  • Pre-categorization rules (transfer patterns, etc.)
  • DB insertion helpers (transactions, account balances, spending periods)
  • Post-parse outlier checks
  • Main entry point: parse_new_statements() — calls bank-specific parsers

Bank-specific parsing lives in separate modules:
  src/parser_td.py  — TD Bank CSV + PDF (chequing + Visa CC)

To add a new bank:
  1. Create src/parser_<bank>.py following the 4-section structure in parser_td.py
  2. Add its parse_csv / parse_pdf / detect_account / extract_statement_dates /
     verify_statement to the import block inside parse_new_statements() below.
"""

import argparse
import hashlib
import json
import logging
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Optional

from dateutil import parser as dateutil_parser
from rich.console import Console
from rich.table import Table

# Allow running as a script from project root
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import CORRECTIONS_FILE, DB_PATH, STATEMENTS_DIR

console = Console()
log = logging.getLogger(__name__)


# ── Date normalisation ─────────────────────────────────────────────────────────

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
        dt = dateutil_parser.parse(raw, dayfirst=False)
    except (ValueError, OverflowError):
        try:
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


# ── Hash ───────────────────────────────────────────────────────────────────────

def compute_hash(date: str, description: str, amount: float, occurrence: int = 0) -> str:
    """MD5 fingerprint for deduplication.

    occurrence allows two legitimately identical transactions in the same file
    (e.g. two parking charges for the same amount on the same day) to coexist.
    """
    raw = f"{date}|{description}|{amount:.2f}|{occurrence}"
    return hashlib.md5(raw.encode()).hexdigest()


# ── Already-imported guard ─────────────────────────────────────────────────────

def is_already_imported(conn: sqlite3.Connection, filename: str) -> bool:
    """Return True if any transaction row already carries this source_file."""
    row = conn.execute(
        "SELECT 1 FROM transactions WHERE source_file = ? LIMIT 1",
        (filename,)
    ).fetchone()
    return row is not None


# ── Personal info scrubbing ────────────────────────────────────────────────────

# Patterns that reveal real names (e-transfers, cheques) — replaced with placeholders.
_SCRUB_PATTERNS = [
    (re.compile(r"(INTERAC\s+e-?TRANSFER\s+(?:SENT\s+TO|RECEIVED\s+FROM))\s+[\w\s\-'\.]{2,40}",
                re.IGNORECASE),
     r"\1 [RECIPIENT]"),
    (re.compile(r"(e-?TFR\s+(?:TO|FROM))\s+[\w\s\-'\.]{2,30}", re.IGNORECASE),
     r"\1 [RECIPIENT]"),
    (re.compile(r"(CHEQUE\s+#?\d+\s+(?:TO|FROM|PAYABLE\s+TO))\s+[\w\s\-'\.]{2,40}",
                re.IGNORECASE),
     r"\1 [PAYEE]"),
    # TD chequing type suffixes: "MERCHANT 9.99_V" → "MERCHANT"
    (re.compile(r"\s+[\d]+\.[\d]{2}_[VF]\s*$", re.IGNORECASE), ""),
    (re.compile(r"\s+_[VF]\s*$", re.IGNORECASE), ""),
]


def scrub_description(desc: str) -> str:
    """Remove personal names from transaction descriptions."""
    for pattern, replacement in _SCRUB_PATTERNS:
        desc = pattern.sub(replacement, desc)
    return desc


# ── Source filename sanitisation ───────────────────────────────────────────────

_ACCT_NUM_RE = re.compile(r"\d{4}[-_]\d{4,10}|\d{8,}")


def sanitize_source_filename(filename: str) -> str:
    """
    Strip account/card number patterns from a filename before storing in the DB.

    Example:
      'CHEQUING_ACCOUNT_XXXX-XXXXXXX_Jan_30-Feb_27_2026.pdf'
      → 'CHEQUING_ACCOUNT_Jan_30-Feb_27_2026.pdf'
    """
    stem   = Path(filename).stem
    suffix = Path(filename).suffix
    cleaned = _ACCT_NUM_RE.sub("", stem)
    cleaned = re.sub(r"[_\-]{2,}", "_", cleaned).strip("_-")
    return cleaned + suffix


# ── Pre-categorization rules ───────────────────────────────────────────────────
# Applied at insert time before the AI categorizer.
# confirmed=1 → AI will NOT overwrite; confirmed=0 → AI/human can still correct.

_PRECATEGORY_RULES: list[tuple[re.Pattern, str, int]] = [
    # Transfers / payments
    (re.compile(r"TDVISAPREAUTHPYMT|TD\s*VISA\s*PREAUTH",             re.IGNORECASE), "transfer", 1),
    (re.compile(r"PREAUTHORIZED\s*PAYMENT|PAYMENT\s*[-–]?\s*THANK\s+YOU", re.IGNORECASE), "transfer", 1),
    (re.compile(r"TD\s*LOC\s*PYMT|TDLOC",                             re.IGNORECASE), "transfer", 1),
    (re.compile(r"SENDE?-?TFR|SEND\s*TRANSFER",                       re.IGNORECASE), "transfer", 0),
    (re.compile(r"CANCELE?-?TFR|CANCEL\s*TRANSFER",                   re.IGNORECASE), "transfer", 1),
    (re.compile(r"RECV\s*TFR|RECEIVE\s*TRANSFER|INTERNET\s*TRANSFER", re.IGNORECASE), "transfer", 1),
]


def precategorize(description: str) -> tuple[str, int]:
    """
    Check description against known patterns.
    Returns (category, confirmed).  Falls back to ('unknown', 0).
    """
    for pattern, category, confirmed in _PRECATEGORY_RULES:
        if pattern.search(description):
            return category, confirmed
    return "unknown", 0


# ── Corrections helpers ────────────────────────────────────────────────────────

def _load_corrections_for_parser() -> dict:
    """Load corrections.json for use at import time.  Returns {} on any error."""
    try:
        raw = json.loads(CORRECTIONS_FILE.read_text(encoding="utf-8"))
        return {k.upper(): v for k, v in raw.items() if not k.startswith("_")}
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        log.warning("corrections.json is malformed — skipping: %s", exc)
        return {}


def _corrections_category(description: str, corrections: dict) -> Optional[tuple[str, Optional[str], int]]:
    """Return (category, subcategory, confirmed=1) if any corrections key matches, else None."""
    desc_upper = description.upper()
    for key, override in corrections.items():
        if key in desc_upper:
            return override.get("category", "other"), override.get("subcategory"), 1
    return None


# ── DB insertion ───────────────────────────────────────────────────────────────

def insert_transactions(
    conn: sqlite3.Connection,
    rows: list[dict],
    source_file: str,
    account: str = "unknown",
) -> dict:
    """
    Insert normalised transaction rows into the DB.

    Category resolution order (first match wins):
      1. corrections.json  — user-defined rules, confirmed=1
      2. precategorize()   — hardcoded regex rules
      3. 'unknown'         — AI categorizer fills this in later

    Skips rows whose hash already exists (dedup).
    Returns counts: inserted, skipped, failed.
    """
    inserted = skipped = failed = 0
    corrections = _load_corrections_for_parser()
    occurrence_counter: dict[tuple, int] = {}

    for row in rows:
        try:
            key        = (row["date"], row["description"], row["amount"])
            occurrence = occurrence_counter.get(key, 0)
            occurrence_counter[key] = occurrence + 1
            h = compute_hash(row["date"], row["description"], row["amount"], occurrence)

            if conn.execute("SELECT 1 FROM transactions WHERE hash = ?", (h,)).fetchone():
                skipped += 1
                continue

            corr = _corrections_category(row["description"], corrections)
            if corr:
                category, subcategory, confirmed = corr
            else:
                category, confirmed = precategorize(row["description"])
                subcategory = None

            conn.execute(
                """
                INSERT INTO transactions
                  (date, description, amount, type, account, category, subcategory, confirmed, source_file, hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (row["date"], row["description"], row["amount"],
                 row["type"], account, category, subcategory, confirmed, source_file, h),
            )
            inserted += 1

        except Exception as e:
            log.debug("Insert failed for row %s: %s", row, e)
            failed += 1

    conn.commit()
    return {"inserted": inserted, "skipped": skipped, "failed": failed}


# ── Account balance persistence ────────────────────────────────────────────────

def save_account_balance(
    conn: sqlite3.Connection,
    account: str,
    statement_month: str,
    opening: Optional[float],
    closing: Optional[float] = None,
    source_file: str = "",
    statement_start: Optional[str] = None,
    statement_end: Optional[str] = None,
) -> None:
    """Persist opening/closing balance and official statement date range for an account."""
    conn.execute(
        """
        INSERT OR REPLACE INTO account_balances
            (account, statement_month, opening_balance, closing_balance,
             statement_start, statement_end, source_file)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (account, statement_month, opening, closing, statement_start, statement_end, source_file),
    )
    conn.commit()


def upsert_spending_periods(
    conn: sqlite3.Connection,
    rows: list[dict],
    stmt_start: Optional[str] = None,
    stmt_end: Optional[str] = None,
) -> None:
    """
    Ensure every calendar month touched by these rows exists in spending_periods,
    then cascade completeness flags:

      1. Seed spending_periods — INSERT OR IGNORE each calendar month.
      2. Update covers_month per (account, month) in account_balances.
      3. Update is_complete per month in spending_periods.

    is_complete = 1 when ALL accounts with a balance row for that month
    also have covers_month = 1 (i.e., every imported statement covers the
    full calendar month).
    """
    import calendar as _cal

    months_seen: set[str] = set()
    for row in rows:
        date_str = row.get("date", "")
        if date_str and len(date_str) >= 7:
            months_seen.add(date_str[:7])

    for label in months_seen:
        year, month = int(label[:4]), int(label[5:7])
        conn.execute(
            "INSERT OR IGNORE INTO spending_periods (period_label, year, month) VALUES (?, ?, ?)",
            (label, year, month),
        )
    conn.commit()

    # Update covers_month for every (account, statement_month) pair
    acct_months = conn.execute(
        "SELECT account, statement_month FROM account_balances"
    ).fetchall()

    for account, statement_month in acct_months:
        y, m = int(statement_month[:4]), int(statement_month[5:7])
        last_day_str = f"{y}-{m:02d}-{_cal.monthrange(y, m)[1]:02d}"

        covering = conn.execute(
            """
            SELECT 1 FROM account_balances
            WHERE account = ?
              AND statement_start IS NOT NULL
              AND statement_end   IS NOT NULL
              AND statement_start <= ?
              AND statement_end   >= ?
            LIMIT 1
            """,
            (account, last_day_str, last_day_str),
        ).fetchone()

        conn.execute(
            "UPDATE account_balances SET covers_month = ? WHERE account = ? AND statement_month = ?",
            (1 if covering else 0, account, statement_month),
        )
    conn.commit()

    conn.execute("""
        UPDATE spending_periods
        SET is_complete = (
            SELECT CASE
                WHEN COUNT(*) = 0 THEN 0
                WHEN MIN(covers_month) = 1 THEN 1
                ELSE 0
            END
            FROM account_balances
            WHERE statement_month = spending_periods.period_label
        )
    """)
    conn.commit()


# ── Post-parse sanity checks ───────────────────────────────────────────────────

def _check_outliers(rows: list[dict]) -> list[dict]:
    """
    Flag debit amounts that are suspiciously large relative to the median.

    Catches parser bugs where two amounts are merged into one
    (e.g. $62.99 + $45,000 → one phantom $45,062.99 debit).
    Also flags any single debit ≥ $10,000 for a manual sanity check.

    Returns a list of warning dicts: {description, amount, date, reason}.
    """
    warnings = []
    debits = [r["amount"] for r in rows if r.get("type") == "debit" and r["amount"] > 0]
    if not debits:
        return warnings

    sorted_debits = sorted(debits)
    mid    = len(sorted_debits) // 2
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


# ── Main entry point ───────────────────────────────────────────────────────────

def parse_new_statements(
    statements_dir: Path = STATEMENTS_DIR,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """
    Scan statements_dir for .pdf and .csv files not yet imported.
    Parse each one and insert into the DB.

    Bank-specific parsers are imported here.  To add a new bank:
      from src.parser_<bank> import parse_csv, parse_pdf, detect_account, ...

    Returns a list of summary dicts, one per file.
    """
    # Bank-specific parsers — add more bank imports here as you expand
    from src.parser_td import (
        parse_csv, parse_pdf, detect_account,
        extract_statement_dates, verify_statement,
    )

    conn    = sqlite3.connect(db_path)
    results = []

    files = sorted(
        list(statements_dir.glob("*.pdf")) + list(statements_dir.glob("*.csv"))
    )

    if not files:
        console.print(f"[dim]No statement files found in {statements_dir}[/dim]")
        conn.close()
        return []

    for file_path in files:
        filename      = file_path.name
        safe_filename = sanitize_source_filename(filename)

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

        suffix     = file_path.suffix.lower()
        stmt_start: Optional[str] = None
        stmt_end:   Optional[str] = None

        if suffix == ".csv":
            rows          = parse_csv(file_path)
            parse_dropped = 0
        elif suffix == ".pdf":
            rows, parse_dropped = parse_pdf(file_path)
            stmt_start, stmt_end = extract_statement_dates(file_path, account)
            if stmt_start and stmt_end:
                console.print(f"  [dim]Statement period:[/dim] {stmt_start} → {stmt_end}")
            else:
                console.print(
                    "  [yellow]⚠ Could not extract statement period dates from PDF header[/yellow]"
                )
        else:
            continue

        # Outlier check
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

        # Balance reconciliation (chequing + creditcard)
        reconciliation = None
        if suffix == ".pdf":
            rec = verify_statement(file_path, account)
            if account == "chequing" and rec["opening_balance"] is not None:
                net_parsed   = sum(
                    r["amount"] * (1 if r["type"] == "credit" else -1) for r in rows
                )
                expected_net = (rec["closing_balance"] or 0) - rec["opening_balance"]
                delta = round(abs(net_parsed - expected_net), 2)
                ok    = delta < 0.05
                mark  = "[green]✓[/green]" if ok else "[bold red]✗[/bold red]"
                console.print(
                    f"  {mark} Balance check:  "
                    f"parsed net [cyan]${net_parsed:+,.2f}[/cyan]  "
                    f"statement net [cyan]${expected_net:+,.2f}[/cyan]"
                    + ("" if ok else f"  [red]Δ ${delta:,.2f} — investigate[/red]")
                )
                reconciliation = {
                    "opening":      rec["opening_balance"],
                    "closing":      rec["closing_balance"],
                    "parsed_net":   round(net_parsed, 2),
                    "expected_net": round(expected_net, 2),
                    "delta":        delta,
                    "ok":           ok,
                }
            elif account == "creditcard" and rec["expected_charges"] is not None:
                total_debits   = sum(r["amount"] for r in rows if r["type"] == "debit")
                total_credits  = sum(r["amount"] for r in rows if r["type"] == "credit")
                delta_charges  = round(abs(total_debits  - rec["expected_charges"]),       2)
                delta_payments = round(abs(total_credits - (rec["expected_payments"] or 0)), 2)
                ok   = delta_charges < 0.05 and delta_payments < 0.05
                mark = "[green]✓[/green]" if ok else "[bold red]✗[/bold red]"
                console.print(
                    f"  {mark} CC check:  "
                    f"charges [cyan]${total_debits:,.2f}[/cyan] / "
                    f"[dim]expected ${rec['expected_charges']:,.2f}[/dim]  "
                    f"payments [cyan]${total_credits:,.2f}[/cyan] / "
                    f"[dim]expected ${rec['expected_payments'] or 0:,.2f}[/dim]"
                    + ("" if ok else "  [red]mismatch — investigate[/red]")
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

        # Persist balance + spending periods
        if reconciliation and rows:
            last_date       = max(r["date"] for r in rows if r.get("date"))
            statement_month = last_date[:7]
            opening = reconciliation.get("opening")
            closing = reconciliation.get("closing") or reconciliation.get("expected_new_bal")
            save_account_balance(
                conn, account, statement_month, opening, closing,
                safe_filename, stmt_start, stmt_end,
            )

        if rows:
            upsert_spending_periods(conn, rows, stmt_start, stmt_end)

        results.append({
            "file":              safe_filename,
            "parsed":            len(rows),
            "inserted":          counts["inserted"],
            "skipped":           counts["skipped"],
            "failed":            counts["failed"],
            "dropped":           parse_dropped,
            "status":            "imported",
            "outlier_warnings":  outlier_warnings,
            "reconciliation":    reconciliation,
        })

    conn.close()
    return results


# ── Dev utilities ──────────────────────────────────────────────────────────────

def _print_results_table(results: list[dict]) -> None:
    """Print a Rich table summarising parse results."""
    table = Table(show_header=True, header_style="bold cyan", border_style="dim")
    table.add_column("File",     style="white",        min_width=24)
    table.add_column("Parsed",   justify="right")
    table.add_column("Inserted", justify="right",      style="bold green")
    table.add_column("Skipped",  justify="right",      style="yellow")
    table.add_column("Failed",   justify="right",      style="red")
    table.add_column("Dropped",  justify="right")
    table.add_column("Status",   style="dim")

    for r in results:
        dropped     = r.get("dropped", 0)
        dropped_str = f"[bold red]{dropped}[/bold red]" if dropped > 0 else "[dim]0[/dim]"
        table.add_row(
            r["file"], str(r["parsed"]), str(r["inserted"]),
            str(r["skipped"]), str(r["failed"]), dropped_str, r.get("status", ""),
        )
    console.print(table)


def reimport_file(file_path: Path, db_path: Path = DB_PATH) -> None:
    """
    Delete all transactions for a previously-imported file and re-parse it.

    Usage:
        python src/parser.py --reimport data/statements/MY_CHEQUING.pdf
    """
    from src.parser_td import parse_csv, parse_pdf, detect_account

    filename = sanitize_source_filename(file_path.name)
    conn     = sqlite3.connect(db_path)

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
    conn   = sqlite3.connect(db_path)
    if suffix == ".csv":
        rows    = parse_csv(file_path)
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


def run_test() -> None:
    """Create a temp CSV with 5 known transactions, parse and verify."""
    console.rule("[bold cyan]Parser self-test[/bold cyan]")

    test_csv_content = """Date,Description,Debit,Credit
2024-03-01,TIM HORTONS #1234,4.75,
2024-03-03,LOBLAWS,87.42,
2024-03-05,PAYROLL DEPOSIT,,2450.00
2024-03-07,NETFLIX,18.99,
2024-03-10,ESSO GAS STATION,65.00,
"""

    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = Path(tmpdir) / "test_statement.csv"
        csv_path.write_text(test_csv_content)

        db_path     = Path(tmpdir) / "test.db"
        schema_path = Path(__file__).parent.parent / "db" / "schema.sql"
        conn        = sqlite3.connect(db_path)
        conn.executescript(schema_path.read_text())
        conn.close()

        console.print("\n[bold]Pass 1 — expect 5 inserted:[/bold]")
        r1 = parse_new_statements(Path(tmpdir), db_path)
        _print_results_table(r1)

        console.print("\n[bold]Pass 2 — expect 0 inserted (already imported):[/bold]")
        r2 = parse_new_statements(Path(tmpdir), db_path)
        _print_results_table(r2)

        conn  = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        conn.close()

        console.print(f"\n[green]DB has {count} transaction(s) — expected 5[/green]")
        if count == 5:
            console.print("[bold green]✓ CSV self-test passed[/bold green]")
        else:
            console.print("[bold red]✗ CSV self-test failed[/bold red]")
            sys.exit(1)

    # Visa PDF smoke test (skipped on clean clone — statements/ not committed)
    console.rule("[bold cyan]Visa PDF smoke test[/bold cyan]")
    from src.parser_td import parse_pdf
    visa_pdf = next(STATEMENTS_DIR.glob("TD_CASH_BACK_VISA*.pdf"), None)
    if visa_pdf is None:
        console.print("[yellow]  Skipped: no TD Visa PDF found in data/statements/[/yellow]")
    else:
        rows, dropped = parse_pdf(visa_pdf)
        if dropped != 0 or not rows:
            console.print("[bold red]✗ Visa PDF smoke test failed[/bold red]")
            sys.exit(1)
        console.print(
            f"[bold green]✓ Visa PDF smoke test passed[/bold green] — "
            f"{len(rows)} transaction(s) parsed, 0 dropped"
        )


# ── CLI entry point ────────────────────────────────────────────────────────────

def _argparse_main() -> None:
    """Invoked by `python src/parser.py` or `python src/parser_core.py`."""
    from src.parser_td import inspect_pdf

    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument("--test",     action="store_true", help="Run self-test with fake CSV data")
    arg_parser.add_argument("--inspect",  metavar="FILE",      help="Debug: dump raw pdfplumber output for a PDF")
    arg_parser.add_argument("--reimport", metavar="FILE",      help="Delete existing rows for a file and re-parse")
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
        console.print(f"\n[bold green]Total inserted: {total}[/bold green]")


if __name__ == "__main__":
    _argparse_main()

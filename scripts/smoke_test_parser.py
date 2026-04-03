#!/usr/bin/env python3
"""
scripts/smoke_test_parser.py — End-to-end parser smoke test.

Drop a real TD statement in data/statements/ (or pass as argument) and run:

    python scripts/smoke_test_parser.py                           # auto-discover
    python scripts/smoke_test_parser.py path/to/statement.pdf    # specific file
    python scripts/smoke_test_parser.py --dir path/to/folder     # specific folder

Five phases:
  ① PARSE   — runs the full parse pipeline on your real file
  ② PREVIEW — table of the first 10 normalised transactions
  ③ INSERT  — imports into a temp DB (not finance.db), shows counts + balance check
  ④ DEDUP   — file-level skip + hash-level re-insert both return 0
  ⑤ SUMMARY — statement dates, balances, category split, timing

All writes go to a throwaway temp DB — finance.db is never touched.
"""

import argparse
import sqlite3
import sys
import time
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table
from rich import box

from config import SCHEMA_FILE, STATEMENTS_DIR
from db.init_db import MIGRATIONS
from src.parser_td import (
    detect_account,
    parse_csv,
    parse_pdf,
    extract_statement_dates,
    verify_statement,
)
from src.parser_core import (
    insert_transactions,
    save_account_balance,
    upsert_spending_periods,
    sanitize_source_filename,
    is_already_imported,
)

console = Console()

PASS = "[bold green]✓[/bold green]"
FAIL = "[bold red]✗[/bold red]"


# ── Temp DB setup ──────────────────────────────────────────────────────────────

def _init_temp_db(db_path: Path) -> None:
    """Apply schema + all additive migrations to a fresh SQLite file."""
    schema = SCHEMA_FILE.read_text()
    conn   = sqlite3.connect(db_path)
    conn.executescript(schema)
    for _, sql in MIGRATIONS:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


def _get_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ── Phase helpers ──────────────────────────────────────────────────────────────

def _phase(n: int, title: str) -> None:
    console.print(f"\n[bold cyan]{'─'*60}[/bold cyan]")
    console.print(f"[bold white]  {n}  {title}[/bold white]")
    console.print(f"[bold cyan]{'─'*60}[/bold cyan]")


def _find_statement(args) -> Path:
    """Return the statement file to test, or exit with a helpful message."""
    if args.file:
        p = Path(args.file)
        if not p.exists():
            console.print(f"[red]File not found: {p}[/red]")
            sys.exit(1)
        return p

    search_dir = Path(args.dir) if args.dir else STATEMENTS_DIR
    candidates = sorted(
        list(search_dir.glob("*.pdf")) + list(search_dir.glob("*.csv"))
    )
    if not candidates:
        console.print(
            f"[yellow]No PDF or CSV files found in {search_dir}[/yellow]\n"
            f"Drop a TD statement there and re-run, or pass a path directly:\n"
            f"  [cyan]python scripts/smoke_test_parser.py path/to/statement.pdf[/cyan]"
        )
        sys.exit(1)

    if len(candidates) == 1:
        return candidates[0]

    # Multiple files — let the user pick
    console.print(f"[dim]Found {len(candidates)} statement(s) in {search_dir}:[/dim]")
    for i, p in enumerate(candidates, 1):
        console.print(f"  {i}. {p.name}")
    choice = console.input("\n[cyan]Enter number (or press Enter for [1]):[/cyan] ").strip()
    idx    = (int(choice) - 1) if choice.isdigit() else 0
    return candidates[max(0, min(idx, len(candidates) - 1))]


# ── Main smoke test ────────────────────────────────────────────────────────────

def run_smoke_test(file_path: Path) -> bool:
    """
    Run all five smoke-test phases.  Returns True if all passed.
    """
    t_start   = time.time()
    all_ok    = True
    safe_name = sanitize_source_filename(file_path.name)
    suffix    = file_path.suffix.lower()
    account   = detect_account(file_path.name)

    console.rule(f"[bold cyan]PARSER SMOKE TEST[/bold cyan]")
    console.print(f"[dim]  File:    {file_path.name}[/dim]")
    console.print(f"[dim]  Account: {account}[/dim]")

    # ── ① PARSE ───────────────────────────────────────────────────────────────
    _phase(1, "PARSE")

    stmt_start = stmt_end = None
    if suffix == ".csv":
        rows         = parse_csv(file_path)
        parse_dropped = 0
    elif suffix == ".pdf":
        rows, parse_dropped = parse_pdf(file_path)
        stmt_start, stmt_end = extract_statement_dates(file_path, account)
    else:
        console.print(f"[red]Unsupported file type: {suffix}[/red]")
        return False

    parsed_total = len(rows)
    parse_ok     = parsed_total > 0

    console.print(f"  Parsed:    [{'green' if parse_ok else 'red'}]{parsed_total}[/] transactions")
    if parse_dropped:
        console.print(f"  [yellow]Dropped:   {parse_dropped} rows (see warnings above)[/yellow]")
    if stmt_start and stmt_end:
        console.print(f"  Period:    {stmt_start} → {stmt_end}  {PASS}")
    else:
        console.print(f"  Period:    [yellow]could not extract from PDF header[/yellow]")

    if not parse_ok:
        console.print(f"  {FAIL} No transactions parsed — aborting.")
        return False

    console.print(f"  {PASS if not parse_dropped else '[yellow]~[/yellow]'} "
                  f"Parsed {parsed_total} rows"
                  + (f", {parse_dropped} dropped" if parse_dropped else ""))
    all_ok = all_ok and parse_ok

    # ── ② PREVIEW ─────────────────────────────────────────────────────────────
    _phase(2, f"PREVIEW  (first {min(10, parsed_total)} of {parsed_total} transactions)")

    tbl = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold dim")
    tbl.add_column("Date",        style="dim",          min_width=12)
    tbl.add_column("Description",                       min_width=36)
    tbl.add_column("Amount",      justify="right",      style="cyan")
    tbl.add_column("Type",        justify="center",     min_width=8)

    for r in rows[:10]:
        txn_type  = r.get("type", "")
        type_color = "green" if txn_type == "credit" else "white"
        tbl.add_row(
            r.get("date", ""),
            r.get("description", "")[:55],
            f"${r.get('amount', 0):,.2f}",
            f"[{type_color}]{txn_type}[/{type_color}]",
        )

    console.print(tbl)

    # Spot-check: every row has a valid ISO date and positive amount
    bad_dates   = [r for r in rows if not r.get("date") or len(r["date"]) != 10]
    bad_amounts = [r for r in rows if not isinstance(r.get("amount"), (int, float)) or r["amount"] <= 0]

    if bad_dates:
        console.print(f"  {FAIL} {len(bad_dates)} row(s) have invalid dates")
        all_ok = False
    else:
        console.print(f"  {PASS} All dates normalised to YYYY-MM-DD")

    if bad_amounts:
        console.print(f"  {FAIL} {len(bad_amounts)} row(s) have missing/zero amounts")
        all_ok = False
    else:
        console.print(f"  {PASS} All amounts are positive floats")

    # ── ③ DB INSERT ───────────────────────────────────────────────────────────
    _phase(3, "DB INSERT  (temp DB — finance.db untouched)")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_db = Path(f.name)

    _init_temp_db(tmp_db)
    console.print(f"  [dim]Temp DB: {tmp_db}[/dim]")

    conn   = _get_conn(tmp_db)
    counts = insert_transactions(conn, rows, safe_name, account)
    conn.close()

    inserted = counts["inserted"]
    skipped  = counts["skipped"]
    failed   = counts["failed"]

    insert_ok = inserted > 0 and failed == 0

    console.print(
        f"  Inserted:  [green]{inserted}[/green]  "
        f"Skipped: [yellow]{skipped}[/yellow]  "
        f"Failed: [{'red' if failed else 'dim'}]{failed}[/{'red' if failed else 'dim'}]"
    )

    if not insert_ok:
        console.print(f"  {FAIL} Insert returned 0 rows or had failures")
        all_ok = False
    else:
        console.print(f"  {PASS} {inserted} rows stored in temp DB")

    # Balance reconciliation
    if suffix == ".pdf":
        rec = verify_statement(file_path, account)

        if account == "chequing" and rec["opening_balance"] is not None:
            net_parsed   = sum(r["amount"] * (1 if r["type"] == "credit" else -1) for r in rows)
            expected_net = (rec["closing_balance"] or 0) - rec["opening_balance"]
            delta        = round(abs(net_parsed - expected_net), 2)
            bal_ok       = delta < 0.05
            mark         = PASS if bal_ok else FAIL
            console.print(
                f"  {mark} Balance:   "
                f"parsed net [cyan]${net_parsed:+,.2f}[/cyan]  "
                f"statement net [cyan]${expected_net:+,.2f}[/cyan]"
                + (f"  [yellow]Δ ${delta:,.2f}[/yellow]" if not bal_ok else f"  [dim]Δ $0.00[/dim]")
            )
            if not bal_ok:
                all_ok = False

        elif account == "creditcard" and rec["expected_charges"] is not None:
            total_debits  = sum(r["amount"] for r in rows if r["type"] == "debit")
            total_credits = sum(r["amount"] for r in rows if r["type"] == "credit")
            d_charges     = round(abs(total_debits  - rec["expected_charges"]),       2)
            d_payments    = round(abs(total_credits - (rec["expected_payments"] or 0)), 2)
            bal_ok        = d_charges < 0.05 and d_payments < 0.05
            mark          = PASS if bal_ok else FAIL
            console.print(
                f"  {mark} CC balance: "
                f"charges [cyan]${total_debits:,.2f}[/cyan] (expected ${rec['expected_charges']:,.2f})  "
                f"payments [cyan]${total_credits:,.2f}[/cyan] (expected ${rec['expected_payments'] or 0:,.2f})"
            )
            if not bal_ok:
                all_ok = False
        else:
            console.print("  [dim]Balance check: N/A (no opening balance in statement)[/dim]")

    # Save balance + spending periods for summary phase
    if suffix == ".pdf":
        rec = verify_statement(file_path, account)
        opening = rec.get("opening_balance")
        closing = rec.get("closing_balance") or rec.get("expected_new_bal")
        if rows and (opening or closing):
            last_date       = max(r["date"] for r in rows if r.get("date"))
            statement_month = last_date[:7]
            conn2           = _get_conn(tmp_db)
            save_account_balance(
                conn2, account, statement_month, opening, closing,
                safe_name, stmt_start, stmt_end,
            )
            upsert_spending_periods(conn2, rows, stmt_start, stmt_end)
            conn2.close()

    # ── ④ DEDUP CHECK ────────────────────────────────────────────────────────
    _phase(4, "DEDUP CHECK")

    # Pass A — file-level skip (source_file already in DB)
    conn3 = _get_conn(tmp_db)
    file_already_imported = is_already_imported(conn3, safe_name)
    conn3.close()

    if file_already_imported:
        console.print(f"  {PASS} File-level:  is_already_imported() = True — would skip entire file")
    else:
        console.print(f"  {FAIL} File-level:  source_file not found in DB after insert")
        all_ok = False

    # Pass B — hash-level dedup (bypass file check, re-insert same rows under new name)
    conn4         = _get_conn(tmp_db)
    counts_hash   = insert_transactions(conn4, rows, "smoke_test_duplicate.pdf", account)
    conn4.close()

    hash_skipped  = counts_hash["skipped"]
    hash_inserted = counts_hash["inserted"]
    hash_ok       = hash_inserted == 0 and hash_skipped == parsed_total

    if hash_ok:
        console.print(
            f"  {PASS} Hash-level:  re-insert under new filename → "
            f"0 inserted, {hash_skipped} skipped (all hashes exist)"
        )
    else:
        console.print(
            f"  {FAIL} Hash-level:  expected 0 inserted, got {hash_inserted} "
            f"(skipped={hash_skipped}, expected={parsed_total})"
        )
        all_ok = False

    # ── ⑤ SUMMARY ────────────────────────────────────────────────────────────
    _phase(5, "SUMMARY")

    elapsed = time.time() - t_start

    # Category breakdown from temp DB
    conn5    = _get_conn(tmp_db)
    cat_rows = conn5.execute("""
        SELECT category, COUNT(*) AS n
        FROM transactions
        WHERE source_file = ?
        GROUP BY category
        ORDER BY n DESC
    """, (safe_name,)).fetchall()
    conn5.close()

    if stmt_start and stmt_end:
        console.print(f"  Statement period:  {stmt_start} → {stmt_end}")
    if suffix == ".pdf":
        rec2 = verify_statement(file_path, account)
        if rec2.get("opening_balance") is not None:
            console.print(f"  Opening balance:   ${rec2['opening_balance']:,.2f}")
            console.print(f"  Closing balance:   ${rec2['closing_balance']:,.2f}")

    console.print(f"  Transactions:      {inserted} stored, {parse_dropped} dropped")

    if cat_rows:
        cat_str = "  ".join(f"{r['category']}={r['n']}" for r in cat_rows)
        console.print(f"  Categories:        {cat_str}")
        if any(r["category"] == "unknown" for r in cat_rows):
            console.print("  [dim]  → run `POST /api/run-categorizer` to classify unknown rows[/dim]")

    console.print(f"  Elapsed:           {elapsed:.1f}s")

    # Clean up temp DB
    tmp_db.unlink(missing_ok=True)

    # ── Final verdict ─────────────────────────────────────────────────────────
    console.print()
    if all_ok:
        console.rule(f"[bold green]ALL CHECKS PASSED ✓[/bold green]")
    else:
        console.rule(f"[bold red]SOME CHECKS FAILED ✗[/bold red]")

    return all_ok


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="End-to-end parser smoke test for a real TD statement.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/smoke_test_parser.py                              # auto-discover
  python scripts/smoke_test_parser.py data/statements/MY.pdf      # specific file
  python scripts/smoke_test_parser.py --dir path/to/folder        # specific folder
        """,
    )
    parser.add_argument("file",  nargs="?",           help="Path to a PDF or CSV statement")
    parser.add_argument("--dir", metavar="DIR",        help="Folder to auto-discover statements from")
    args = parser.parse_args()

    file_path = _find_statement(args)
    console.print(f"\n[dim]Testing: {file_path}[/dim]\n")

    ok = run_smoke_test(file_path)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

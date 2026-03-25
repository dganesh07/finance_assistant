"""
run.py — Quick DB check and statement import.

Run:  python run.py

Scans data/statements/ for new PDFs/CSVs, parses them, and inserts raw
transactions into the DB (category = 'unknown', confirmed = 0).

For the full pipeline (parse → categorize → review → save), use:
  python scripts/ingest.py data/statements/<file.pdf>

To wipe and re-import everything (e.g. after a parser fix):
  python scripts/reset_and_reimport.py
"""

import json
import sqlite3
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from config import DB_PATH, BILLS_FILE, STATEMENTS_DIR
from db.init_db import initialize_db
from src.parser import parse_new_statements, _print_results_table

console = Console()


# ── Bills ──────────────────────────────────────────────────────────────────────

def load_bills() -> list[dict]:
    """Load and return the list of bills from bills.local.json."""
    try:
        return json.loads(BILLS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        console.print("[yellow]⚠  bills.local.json not found — copy bills.example.json and fill in.[/yellow]")
        return []


def print_bills(bills: list[dict]) -> None:
    """Render bills as a Rich table in the terminal."""
    table = Table(
        title="[bold cyan]Monthly Bills[/bold cyan]",
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
    )
    table.add_column("Name",      style="white",  min_width=16)
    table.add_column("Amount",    justify="right", style="bold green")
    table.add_column("Frequency", style="dim")
    table.add_column("Autopay",   justify="center")
    table.add_column("Due Day",   justify="center")
    table.add_column("Account",   style="dim")

    total = 0.0
    for bill in bills:
        amount = bill["amount"]
        total += amount
        due = str(bill["due_day"]) if bill.get("due_day") else "—"
        autopay = "[green]yes[/green]" if bill.get("autopay") else "[red]no[/red]"
        table.add_row(
            bill["name"],
            f"${amount:,.2f}",
            bill.get("frequency", "monthly"),
            autopay,
            due,
            bill.get("account", "—"),
        )

    # Totals footer row
    table.add_section()
    table.add_row(
        "[bold]TOTAL[/bold]",
        f"[bold yellow]${total:,.2f}[/bold yellow]",
        "", "", "", "",
    )

    console.print(table)
    console.print()


# ── DB tables ──────────────────────────────────────────────────────────────────

def print_db_tables() -> None:
    """Query the SQLite DB and print the table names that exist."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
    )
    tables = [row[0] for row in cursor.fetchall()]
    conn.close()

    table = Table(
        title="[bold magenta]DB Tables[/bold magenta]",
        show_header=True,
        header_style="bold magenta",
        border_style="dim",
    )
    table.add_column("Table", style="white")
    table.add_column("Status", justify="center")

    for t in tables:
        table.add_row(t, "[green]exists[/green]")

    console.print(table)
    console.print()


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    """
    Entry point: initialize the DB, display bills, confirm DB tables exist,
    then parse any new statement files found in data/statements/.
    """
    console.print()
    console.print(Panel(
        Text("FINANCE AGENT", justify="center", style="bold green"),
        border_style="green",
    ))
    console.print()

    # Step 1: Init DB
    initialize_db()
    console.print("[bold green]Finance Agent ready. DB initialized.[/bold green]")
    console.print(f"[dim]DB path: {DB_PATH}[/dim]")
    console.print()

    # Step 2: Load and display bills
    bills = load_bills()
    if bills:
        print_bills(bills)

    # Step 3: Confirm DB tables exist
    print_db_tables()

    # Step 4: Parse any new statement files
    console.rule("[bold cyan]Parsing Statements[/bold cyan]")
    console.print(f"[dim]Scanning: {STATEMENTS_DIR}[/dim]\n")

    results = parse_new_statements()

    if results:
        _print_results_table(results)
        total_new  = sum(r["inserted"] for r in results)
        total_drop = sum(r.get("dropped", 0) for r in results)
        console.print(f"\n[bold green]{total_new} new transaction(s) added to DB.[/bold green]")
        if total_drop:
            console.print(
                f"[bold yellow]⚠ {total_drop} row(s) dropped during parsing.[/bold yellow]  "
                "Run [cyan]python src/parser.py --inspect <file>[/cyan] to investigate."
            )
    else:
        console.print("[dim]No statement files found. Drop a PDF or CSV into data/statements/[/dim]")

    console.print()


if __name__ == "__main__":
    main()

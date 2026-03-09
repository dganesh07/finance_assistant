"""
run.py — Main entry point for the Finance Agent.

Run:  python run.py

Current flow (Phase 1 — scaffold):
  1. Initialize the SQLite DB if it doesn't exist
  2. Print confirmation + loaded bills
  3. Print the DB tables that exist

Future flow (Phase 2+):
  4. Scan data/statements/ for new PDFs/CSVs and parse them
  5. Categorize new transactions with AI (Ollama)
  6. Generate and print a spending report
"""

import json
import sqlite3
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from config import DB_PATH, BILLS_FILE
from db.init_db import initialize_db

console = Console()


# ── Bills ──────────────────────────────────────────────────────────────────────

def load_bills() -> list[dict]:
    """Load and return the list of bills from bills.json."""
    with open(BILLS_FILE, "r") as f:
        return json.load(f)


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
    console.print()
    console.print(Panel(
        Text("FINANCE AGENT", justify="center", style="bold green"),
        subtitle="[dim]Phase 1 — Scaffold[/dim]",
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
    print_bills(bills)

    # Step 3: Confirm DB tables exist
    print_db_tables()

    console.print("[dim]Drop PDFs or CSVs into data/statements/ — Phase 2 will parse them.[/dim]")
    console.print()


if __name__ == "__main__":
    main()

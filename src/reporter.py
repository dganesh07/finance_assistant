"""
src/reporter.py — Terminal report printer using Rich.

PHASE 5 IMPLEMENTATION PLAN:
  - Accept categorized transactions and a date range
  - Aggregate spend by category (sum of debits per category)
  - Compare vs prior period if data exists (month-over-month delta)
  - Compare actual spend vs bills.json fixed costs
  - Call context_builder to build AI context, then call Ollama for narrative
  - Render with Rich:
      * Header with period dates and total spend
      * Spending breakdown table (category | amount | % of total | vs last month)
      * Bills status table (paid? on time? upcoming?)
      * AI narrative paragraph with observations and flags
      * To-do / action items list (saved to todo_items table)
  - Persist the report run to the reports table in DB

DEPENDENCIES: rich, sqlite3 (stdlib)
"""

from rich.console import Console
from rich.table import Table

console = Console()


def print_report(period_start: str, period_end: str) -> None:
    """
    Generate and print a full spending report to the terminal.

    Args:
        period_start: ISO date string for the report window start.
        period_end:   ISO date string for the report window end.
    """
    # TODO (Phase 5): call context_builder.get_transactions_for_period()
    # TODO (Phase 5): aggregate totals by category
    # TODO (Phase 5): build spending breakdown rich Table
    # TODO (Phase 5): call context_builder.build_context() + ollama for narrative
    # TODO (Phase 5): render narrative panel
    # TODO (Phase 5): extract and display todo_items, save to DB
    # TODO (Phase 5): save report row to reports table
    raise NotImplementedError("reporter.py — Phase 5 will implement this.")


def aggregate_by_category(transactions: list[dict]) -> dict[str, float]:
    """
    Sum transaction amounts grouped by category.

    Args:
        transactions: List of transaction dicts with 'category' and 'amount'.

    Returns:
        Dict mapping category name → total amount spent.
    """
    # TODO (Phase 5): filter to debits only, group and sum
    raise NotImplementedError


def render_spending_table(totals: dict[str, float]) -> Table:
    """
    Build a Rich Table showing category spending breakdown.

    Args:
        totals: Dict of category → total amount.

    Returns:
        A configured Rich Table object (not yet printed).
    """
    # TODO (Phase 5): sort by amount desc, add % column, colour-code high spend
    raise NotImplementedError

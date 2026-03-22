"""
src/reporter.py — Spending report generator.

Report generation will live in the dashboard backend (api.py) as a
GET /api/report endpoint. The React frontend renders the output.

The AI narrative piece (Ollama call) will use context_builder.build_context()
to assemble the prompt, then call the model for a spend summary and action list.

This stub remains as a placeholder for any CLI-only report fallback.

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
    raise NotImplementedError("reporter — implemented in api.py (dashboard backend)")


def aggregate_by_category(transactions: list[dict]) -> dict[str, float]:
    """
    Sum transaction amounts grouped by category.

    Args:
        transactions: List of transaction dicts with 'category' and 'amount'.

    Returns:
        Dict mapping category name → total amount spent.
    """
    raise NotImplementedError


def render_spending_table(totals: dict[str, float]) -> Table:
    """
    Build a Rich Table showing category spending breakdown.

    Args:
        totals: Dict of category → total amount.

    Returns:
        A configured Rich Table object (not yet printed).
    """
    raise NotImplementedError

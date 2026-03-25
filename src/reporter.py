"""
src/reporter.py — Placeholder for the AI-powered spending report agent.

This module is a stub. Full report generation lives in api.py (GET /api/report),
where context_builder.build_context() assembles the prompt and an Ollama call
produces the spend summary and action list rendered by the React frontend.

This file exists as a placeholder for any future CLI-only report fallback and
as a home for shared report-related types and exceptions.

DEPENDENCIES: rich, sqlite3 (stdlib)
"""

from rich.console import Console
from rich.table import Table

console = Console()


class ReportError(Exception):
    """Raised when report generation fails or is not yet implemented."""


def generate_report(context: str) -> str:
    """
    Generate an AI-powered spending report from a pre-assembled context string.

    Args:
        context: The full financial context block produced by
                 context_builder.build_context(). This is injected into the
                 LLM prompt as the system/user context.

    Returns:
        A plain-text spending summary and action list produced by the LLM.

    Raises:
        NotImplementedError: This function is not yet implemented in the CLI.
            Use the dashboard API endpoint GET /api/report instead, which
            calls context_builder.build_context() and passes the result to
            the configured Ollama model.
    """
    raise NotImplementedError(
        "generate_report() is not implemented in the CLI reporter. "
        "Use the dashboard API (GET /api/report) or call context_builder.build_context() "
        "and pass the result directly to your Ollama model."
    )


def print_report(period_start: str, period_end: str) -> None:
    """
    Generate and print a full spending report to the terminal.

    Args:
        period_start: ISO date string for the report window start.
        period_end:   ISO date string for the report window end.

    Raises:
        NotImplementedError: Implemented in api.py (dashboard backend).
    """
    raise NotImplementedError("reporter — implemented in api.py (dashboard backend)")


def aggregate_by_category(transactions: list[dict]) -> dict[str, float]:
    """
    Sum transaction amounts grouped by category.

    Args:
        transactions: List of transaction dicts with 'category' and 'amount' keys.

    Returns:
        Dict mapping category name → total amount spent.

    Raises:
        NotImplementedError: Not yet implemented.
    """
    raise NotImplementedError


def render_spending_table(totals: dict[str, float]) -> Table:
    """
    Build a Rich Table showing category spending breakdown.

    Args:
        totals: Dict of category → total amount.

    Returns:
        A configured Rich Table object (not yet printed).

    Raises:
        NotImplementedError: Not yet implemented.
    """
    raise NotImplementedError

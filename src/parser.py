"""
src/parser.py — Backwards-compatible re-export shim.

All logic has moved to:
  src/parser_core.py  — bank-agnostic utilities, DB insertion, main entry point
  src/parser_td.py    — TD Bank CSV + PDF parsing (4-section structure)

This file re-exports everything so existing callers (api.py, run.py, tests/)
continue to work without changes.

To add a new bank: create src/parser_<bank>.py and register it in
parse_new_statements() inside parser_core.py.
"""

# ruff: noqa: F401  (re-exports are intentionally unused here)

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.parser_core import (
    normalise_date,
    compute_hash,
    is_already_imported,
    scrub_description,
    sanitize_source_filename,
    precategorize,
    insert_transactions,
    save_account_balance,
    upsert_spending_periods,
    _check_outliers,
    parse_new_statements,
    reimport_file,
    run_test,
    _print_results_table,
    _argparse_main,
)
from src.parser_td import (
    detect_account,
    parse_csv,
    parse_pdf,
    extract_statement_dates,
    verify_statement,
    inspect_pdf,
    _parse_td_header_date,
    _parse_cc_date,
    _CC_PERIOD_RE,
    _CHQ_PERIOD_RE,
)


if __name__ == "__main__":
    _argparse_main()

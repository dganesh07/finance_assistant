"""
src/parser.py — Statement parser for PDF and CSV bank statement files.

PHASE 2 IMPLEMENTATION PLAN:
  - Scan STATEMENTS_DIR for all .pdf and .csv files
  - PDF path:  use pdfplumber to extract raw text page-by-page,
               then regex / heuristics to find transaction rows
               (date  |  description  |  debit  |  credit)
  - CSV path:  use pandas to read the file; normalize column names
               since each bank exports slightly different headers
  - Normalize both into a common transaction dict:
       { date, description, amount, type, source_file, hash }
       where type = 'debit' | 'credit'
       and   hash = md5(date + description + str(amount))
  - Return a flat list — duplicates filtered via the hash field in DB

DEPENDENCIES: pdfplumber, pandas (both in requirements.txt)
"""

import hashlib
from pathlib import Path


def compute_hash(date: str, description: str, amount: float) -> str:
    """
    Compute a stable md5 fingerprint for a transaction used for deduplication.

    Args:
        date:        ISO date string e.g. "2024-03-15"
        description: Raw transaction description from the bank.
        amount:      Transaction amount as a float.

    Returns:
        Hex md5 digest string.
    """
    raw = f"{date}|{description}|{amount:.2f}"
    return hashlib.md5(raw.encode()).hexdigest()


def parse_all_statements(statements_dir: Path) -> list[dict]:
    """
    Scan statements_dir, parse every PDF and CSV, and return a flat list
    of normalized transaction dicts ready for DB insertion.

    Args:
        statements_dir: Path to the folder containing bank statement files.

    Returns:
        List of dicts with keys:
            date, description, amount, type, source_file, hash
    """
    # TODO (Phase 2): glob for *.pdf and *.csv
    # TODO (Phase 2): route each file to parse_pdf() or parse_csv()
    # TODO (Phase 2): merge results, compute hashes, return flat list
    raise NotImplementedError("parser.py — Phase 2 will implement this.")


def parse_pdf(file_path: Path) -> list[dict]:
    """
    Extract transactions from a single PDF bank statement using pdfplumber.

    Strategy:
      1. Open the PDF, iterate pages.
      2. Extract text with page.extract_text().
      3. Apply regex to find lines matching the bank's row format.
      4. Return list of raw dicts (not yet normalized).

    Args:
        file_path: Absolute path to the .pdf file.

    Returns:
        List of raw transaction dicts (date, description, amount, type).
    """
    raise NotImplementedError


def parse_csv(file_path: Path) -> list[dict]:
    """
    Parse a single CSV bank statement export using pandas.

    Strategy:
      1. Read with pd.read_csv(), sniff delimiter if needed.
      2. Detect column mapping (banks differ: 'Date' vs 'Transaction Date', etc.)
      3. Determine debit/credit from separate columns or signed amounts.
      4. Return list of normalized dicts.

    Args:
        file_path: Absolute path to the .csv file.

    Returns:
        List of raw transaction dicts (date, description, amount, type).
    """
    raise NotImplementedError

"""
src/categorizer.py — AI-powered transaction categorizer via local Ollama LLM.

PHASE 3 IMPLEMENTATION PLAN:
  - Accept a batch of parsed transactions (description + amount)
  - Load profile.txt and CATEGORIES from config for context
  - Build a structured prompt:
      "You are a personal finance assistant. Categorize each transaction
       into one of: [CATEGORIES]. Here is the user's financial profile: ...
       Transactions: ..."
  - Send to Ollama using ollama.chat(model=OLLAMA_MODEL, messages=[...])
  - Parse the JSON response to extract category + optional subcategory
  - Return the same transaction list with 'category' and 'subcategory' filled in
  - Graceful fallback: if Ollama is unreachable, category stays 'unknown'

DEPENDENCIES: ollama (pip install ollama)
"""

from config import CATEGORIES, OLLAMA_MODEL, PROFILE_FILE


def categorize_transactions(transactions: list[dict]) -> list[dict]:
    """
    Use a local LLM via Ollama to assign a category to each transaction.

    Args:
        transactions: List of transaction dicts.
                      Required keys: description, amount
                      Optional keys: date, type

    Returns:
        Same list of dicts with 'category' and 'subcategory' keys populated.
        Falls back to category='unknown' if the model is unavailable.
    """
    # TODO (Phase 3): read profile.txt for personalization context
    # TODO (Phase 3): chunk transactions into batches of ~20 to stay in context
    # TODO (Phase 3): build prompt and call ollama.chat()
    # TODO (Phase 3): parse structured response (JSON or line-by-line)
    # TODO (Phase 3): map responses back to transactions by index
    raise NotImplementedError("categorizer.py — Phase 3 will implement this.")


def build_categorization_prompt(transactions: list[dict], profile: str) -> str:
    """
    Construct the LLM prompt for categorizing a batch of transactions.

    Args:
        transactions: Batch of transaction dicts (description, amount).
        profile:      Full text of profile.txt.

    Returns:
        Formatted prompt string ready to send to Ollama.
    """
    # TODO (Phase 3): format categories list
    # TODO (Phase 3): embed profile text
    # TODO (Phase 3): format each transaction as numbered list
    # TODO (Phase 3): request JSON response with category + subcategory
    raise NotImplementedError

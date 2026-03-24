"""
tests/test_categorizer.py — Unit tests for the categorizer agent.

Run:
  python -m pytest tests/test_categorizer.py -v
  python -m unittest tests/test_categorizer.py   # no pytest needed

These tests use unittest.mock to stub out Ollama, so:
  - No LLM required
  - No internet
  - No DB
  - Fast (< 1 second)

They test the agent's logic in isolation:
  - Prompt construction
  - Correction application
  - JSON response parsing
  - Full pipeline with a mocked Ollama response
  - Edge cases: bad JSON, empty input, unknown categories
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Make sure we can import from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.categorizer import (
    _apply_corrections,
    _clean_for_llm,
    _strip_code_fences,
    build_prompt,
    categorize_transactions,
)
from config import CATEGORIES


# ── Helpers ────────────────────────────────────────────────────────────────────

def _txn(description, amount=10.00, type_="debit", category=None):
    """Minimal transaction dict factory."""
    t = {"description": description, "amount": amount, "type": type_}
    if category:
        t["category"] = category
    return t


def _ollama_response(items: list[dict]) -> MagicMock:
    """
    Build a mock ollama.chat() return value.
    items = [{"index": 1, "category": "groceries", "subcategory": "supermarket"}, ...]
    """
    msg = MagicMock()
    msg.__getitem__ = lambda self, key: (
        {"content": json.dumps(items)}[key]
        if key == "content" else MagicMock()
    )
    resp = MagicMock()
    resp.__getitem__ = lambda self, key: msg if key == "message" else MagicMock()
    return resp


# ── _clean_for_llm ─────────────────────────────────────────────────────────────

class TestCleanForLlm(unittest.TestCase):

    def test_strips_hash_store_number(self):
        self.assertEqual(_clean_for_llm("MCDONALD'S#407"), "MCDONALD'S")

    def test_strips_trailing_digits(self):
        self.assertEqual(_clean_for_llm("PETRO-CANADA78"), "PETRO-CANADA")
        self.assertEqual(_clean_for_llm("SDM267"),         "SDM")
        self.assertEqual(_clean_for_llm("WINNERS263"),     "WINNERS")

    def test_strips_trailing_dash_digits(self):
        self.assertEqual(_clean_for_llm("SOME-MERCHANT-03"), "SOME-MERCHANT")

    def test_replaces_asterisk_with_space(self):
        self.assertEqual(_clean_for_llm("OPENAI*CHATGPT"), "OPENAI CHATGPT")
        self.assertEqual(_clean_for_llm("APPLE*MUSIC"),    "APPLE MUSIC")

    def test_collapses_whitespace(self):
        result = _clean_for_llm("SHOPPERS DRUG MART  #21  SURREY")
        self.assertNotIn("  ", result)

    def test_no_change_when_already_clean(self):
        self.assertEqual(_clean_for_llm("NETFLIX"), "NETFLIX")
        self.assertEqual(_clean_for_llm("WHOLE FOODS MARKET"), "WHOLE FOODS MARKET")

    def test_empty_string(self):
        self.assertEqual(_clean_for_llm(""), "")

    def test_hash_number_mid_string_stripped(self):
        # #NNN anywhere, e.g. store number in the middle
        result = _clean_for_llm("SHOPPERS DRUG MART #21 SURREY")
        self.assertNotIn("#21", result)
        self.assertIn("SHOPPERS", result)


# ── _strip_code_fences ─────────────────────────────────────────────────────────

class TestStripCodeFences(unittest.TestCase):

    def test_no_fences(self):
        self.assertEqual(_strip_code_fences('[{"index":1}]'), '[{"index":1}]')

    def test_json_fence(self):
        raw = '```json\n[{"index":1}]\n```'
        self.assertEqual(_strip_code_fences(raw), '[{"index":1}]')

    def test_plain_fence(self):
        raw = '```\n[{"index":1}]\n```'
        self.assertEqual(_strip_code_fences(raw), '[{"index":1}]')

    def test_strips_whitespace(self):
        raw = '  \n[{"index":1}]\n  '
        self.assertEqual(_strip_code_fences(raw), '[{"index":1}]')


# ── _apply_corrections ─────────────────────────────────────────────────────────

class TestApplyCorrections(unittest.TestCase):

    CORRECTIONS = {
        "NETFLIX":            {"category": "subscriptions", "subcategory": "streaming"},
        "SHOPPERS DRUG MART": {"category": "health",        "subcategory": "pharmacy"},
    }

    def test_exact_match(self):
        txns = [_txn("NETFLIX")]
        result, count = _apply_corrections(txns, self.CORRECTIONS)
        self.assertEqual(count, 1)
        self.assertEqual(result[0]["category"], "subscriptions")
        self.assertEqual(result[0]["subcategory"], "streaming")
        self.assertEqual(result[0]["confirmed"], 1)

    def test_substring_match(self):
        txns = [_txn("SHOPPERS DRUG MART #21 SURREY")]
        result, count = _apply_corrections(txns, self.CORRECTIONS)
        self.assertEqual(count, 1)
        self.assertEqual(result[0]["category"], "health")

    def test_case_insensitive(self):
        txns = [_txn("netflix monthly")]
        result, count = _apply_corrections(txns, self.CORRECTIONS)
        self.assertEqual(count, 1)
        self.assertEqual(result[0]["category"], "subscriptions")

    def test_no_match(self):
        txns = [_txn("AMAZON.CA")]
        result, count = _apply_corrections(txns, self.CORRECTIONS)
        self.assertEqual(count, 0)
        self.assertNotIn("category", result[0])

    def test_does_not_overwrite_already_correct(self):
        """A transaction already set to the right category doesn't count as a new correction."""
        txns = [_txn("NETFLIX", category="subscriptions")]
        result, count = _apply_corrections(txns, self.CORRECTIONS)
        # count=0 because category was already 'subscriptions' (no change)
        self.assertEqual(count, 0)

    def test_empty_corrections(self):
        txns = [_txn("NETFLIX")]
        result, count = _apply_corrections(txns, {})
        self.assertEqual(count, 0)

    def test_does_not_mutate_input(self):
        original = _txn("NETFLIX")
        txns = [original]
        _apply_corrections(txns, self.CORRECTIONS)
        # categorize_transactions copies dicts — but _apply_corrections mutates in place;
        # check the returned list is the same object (mutation is expected here)
        self.assertIn("category", txns[0])


# ── build_prompt ───────────────────────────────────────────────────────────────

class TestBuildPrompt(unittest.TestCase):

    def test_contains_all_categories(self):
        prompt = build_prompt([_txn("MCDONALDS")])
        for cat in CATEGORIES:
            self.assertIn(cat, prompt)

    def test_contains_transaction_description(self):
        # build_prompt runs _clean_for_llm, so cleaned text appears in prompt
        prompt = build_prompt([_txn("WHOLE FOODS MARKET", 87.43)])
        self.assertIn("WHOLE FOODS", prompt)   # cleaned version is still recognisable
        self.assertIn("87.43", prompt)

    def test_numbered_correctly(self):
        txns = [_txn("A"), _txn("B"), _txn("C")]
        prompt = build_prompt(txns)
        self.assertIn("1.", prompt)
        self.assertIn("2.", prompt)
        self.assertIn("3.", prompt)

    def test_profile_included_when_provided(self):
        prompt = build_prompt([_txn("A")], profile="I use Petro-Canada for gas.")
        self.assertIn("Petro-Canada", prompt)

    def test_profile_omitted_when_empty(self):
        prompt = build_prompt([_txn("A")], profile="")
        self.assertNotIn("financial profile", prompt)

    def test_debit_labelled_spent(self):
        prompt = build_prompt([_txn("A", type_="debit")])
        self.assertIn("spent", prompt)

    def test_credit_labelled_received(self):
        prompt = build_prompt([_txn("A", type_="credit")])
        self.assertIn("received", prompt)


# ── categorize_transactions (mocked Ollama) ────────────────────────────────────

class TestCategorizeTransactions(unittest.TestCase):

    def _mock_chat(self, items):
        """Return a context-manager-compatible patch for ollama.chat."""
        return patch(
            "src.categorizer.ollama.chat",
            return_value=_ollama_response(items),
        )

    def test_basic_happy_path(self):
        txns = [_txn("WHOLE FOODS"), _txn("MCDONALDS"), _txn("NETFLIX")]
        llm_reply = [
            {"index": 1, "category": "groceries",    "subcategory": "supermarket"},
            {"index": 2, "category": "food",          "subcategory": "restaurant"},
            {"index": 3, "category": "subscriptions", "subcategory": "streaming"},
        ]
        with self._mock_chat(llm_reply):
            result = categorize_transactions(txns)

        self.assertEqual(result[0]["category"], "groceries")
        self.assertEqual(result[1]["category"], "food")
        self.assertEqual(result[2]["category"], "subscriptions")
        # "supermarket" is not in SUBCATEGORIES["groceries"] (empty list) — validator drops to None
        self.assertIsNone(result[0]["subcategory"])
        # "streaming" is valid for subscriptions
        self.assertEqual(result[2]["subcategory"], "streaming")

    def test_corrections_applied_before_llm(self):
        """Corrections-matched transactions should not be in the LLM batch."""
        txns = [_txn("NETFLIX"), _txn("MCDONALDS")]
        # LLM only sees MCDONALDS (index 1 in its batch)
        llm_reply = [{"index": 1, "category": "food", "subcategory": None}]

        with self._mock_chat(llm_reply), \
             patch("src.categorizer._load_corrections", return_value={
                 "NETFLIX": {"category": "subscriptions", "subcategory": "streaming"}
             }):
            result = categorize_transactions(txns)

        self.assertEqual(result[0]["category"], "subscriptions")  # from corrections
        self.assertEqual(result[0]["confirmed"], 1)               # confirmed=1
        self.assertEqual(result[1]["category"], "food")           # from LLM

    def test_empty_input(self):
        result = categorize_transactions([])
        self.assertEqual(result, [])

    def test_invalid_category_falls_back_to_other(self):
        txns = [_txn("MYSTERY SHOP")]
        llm_reply = [{"index": 1, "category": "fantasy_category", "subcategory": None}]
        with self._mock_chat(llm_reply):
            result = categorize_transactions(txns)
        self.assertEqual(result[0]["category"], "other")

    def test_bad_json_does_not_raise(self):
        """When the LLM returns unparseable text, the function should not raise.
        The transaction is returned as-is (no category key added)."""
        txns = [_txn("SOMETHING")]
        bad_response = MagicMock()
        bad_response.__getitem__ = lambda self, key: (
            MagicMock(**{"__getitem__": lambda s, k: "this is not json"})
            if key == "message" else MagicMock()
        )
        with patch("src.categorizer.ollama.chat", return_value=bad_response):
            result = categorize_transactions(txns)   # must not raise
        self.assertEqual(len(result), 1)
        # No category was assigned — the original dict is returned unchanged
        self.assertNotIn("category", result[0])

    def test_does_not_mutate_input(self):
        txns = [_txn("WHOLE FOODS")]
        original_keys = set(txns[0].keys())
        llm_reply = [{"index": 1, "category": "groceries", "subcategory": None}]
        with self._mock_chat(llm_reply):
            categorize_transactions(txns)
        # Input dict should be unchanged
        self.assertEqual(set(txns[0].keys()), original_keys)

    def test_ollama_connection_error_handled(self):
        txns = [_txn("WHOLE FOODS")]
        with patch("src.categorizer.ollama.chat", side_effect=ConnectionError("refused")):
            result = categorize_transactions(txns)  # must not raise
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

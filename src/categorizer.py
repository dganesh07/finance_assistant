"""
src/categorizer.py — AI-powered transaction categorizer via local Ollama LLM.

How it works:
  1. Apply corrections.json overrides first (instant, no LLM call needed).
  2. Load profile.txt for personalization context.
  3. Split remaining uncategorized transactions into batches of BATCH_SIZE.
  4. Build a structured prompt: categories + profile + numbered transaction list.
  5. Send to Ollama — expects a JSON array back, one object per transaction.
  6. Parse the response and stamp each transaction dict with category/subcategory.
  7. Graceful fallback: if Ollama is unreachable, category stays 'unknown'.

Feedback loop — how to teach the agent:
  Edit data/corrections.json and add the merchant name → correct category.
  Corrections are substring-matched (case-insensitive) and always win over the LLM.
  Run: python scripts/add_correction.py 'NETFLIX' subscriptions

Run the transparent test:
  python scripts/test_categorizer.py
"""

import json

import ollama

from config import CATEGORIES, CORRECTIONS_FILE, OLLAMA_MODEL, PROFILE_FILE

BATCH_SIZE = 20  # transactions per LLM call


# ── Corrections (feedback loop) ────────────────────────────────────────────────

def _load_corrections() -> dict:
    """
    Load data/corrections.json — user-defined category overrides.

    Returns a dict of { "SUBSTRING": {"category": ..., "subcategory": ...} }.
    Keys starting with "_" are comments and are ignored.
    """
    try:
        raw = json.loads(CORRECTIONS_FILE.read_text(encoding="utf-8"))
        return {k: v for k, v in raw.items() if not k.startswith("_")}
    except FileNotFoundError:
        return {}


def _apply_corrections(transactions: list[dict], corrections: dict) -> tuple[list[dict], int]:
    """
    Stamp any transaction whose description contains a corrections key.

    Returns the updated list and a count of how many were corrected.
    Corrections are applied before the LLM is called — they always win.
    """
    count = 0
    desc_upper = [(t, t["description"].upper()) for t in transactions]
    for key, override in corrections.items():
        key_upper = key.upper()
        for txn, desc in desc_upper:
            if key_upper in desc and txn.get("category") != override["category"]:
                txn["category"]    = override["category"]
                txn["subcategory"] = override.get("subcategory")
                txn["confirmed"]   = 1   # user-defined = confirmed
                count += 1
    return transactions, count


# ── Profile ────────────────────────────────────────────────────────────────────

def _load_profile() -> str:
    """Return contents of profile.txt, or empty string if unfilled/missing."""
    try:
        text = PROFILE_FILE.read_text(encoding="utf-8").strip()
        if "[YOUR NAME]" in text or "[e.g." in text:
            return ""
        return text
    except FileNotFoundError:
        return ""


# ── LLM helpers ────────────────────────────────────────────────────────────────

def _strip_code_fences(text: str) -> str:
    """Remove ```json ... ``` fences that some models wrap their output in."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(inner).strip()
    return text


def build_prompt(transactions: list[dict], profile: str = "") -> str:
    """
    Build the categorization prompt for one batch.

    Only include transactions that don't yet have a category (corrections
    are applied before this is called, so confirmed ones are skipped).
    """
    cats = ", ".join(CATEGORIES)

    txn_lines = []
    for i, t in enumerate(transactions, start=1):
        direction = "spent" if t.get("type") == "debit" else "received"
        txn_lines.append(
            f'{i}. "{t["description"]}" — ${t["amount"]:.2f} ({direction})'
        )
    txn_block = "\n".join(txn_lines)

    profile_section = (
        f"\n\nUser financial profile (use this to improve accuracy):\n{profile}"
        if profile
        else ""
    )

    return f"""\
You are a personal finance categorizer. Assign each transaction below to exactly \
one category from this list:

{cats}

Rules:
- Reply with ONLY a valid JSON array — no explanation, no markdown, no extra text.
- One object per transaction, in the same order they appear.
- Each object must have:
    "index"      : the transaction number (1-based, matches the list below)
    "category"   : one value from the category list above
    "subcategory": a short optional label (e.g. "supermarket", "streaming") or null
- If unsure, use "other".

Merchant name hints:
- "*" in a merchant name is a payment processor separator — the word before or after it is the real company name (e.g. "APPLE*MUSIC" → Apple Music → subscriptions/streaming).
- Use the dollar amount as a signal: small recurring amounts ($5–$50) with a tech/media company name are almost always subscriptions; large one-off amounts may be shopping, rent, or transfer.
- "received" transactions are likely income or transfer, not spending.{profile_section}

Transactions:
{txn_block}

JSON array:"""


# ── Core function ──────────────────────────────────────────────────────────────

def categorize_transactions(transactions: list[dict]) -> list[dict]:
    """
    Categorize transactions using corrections first, then Ollama for the rest.

    Args:
        transactions: List of transaction dicts.
                      Required keys: description, amount
                      Optional keys: type, date

    Returns:
        Same list (copy) with 'category', 'subcategory', and 'confirmed' populated.
        - Corrections-matched: confirmed=1, no LLM call.
        - LLM-categorized: confirmed=0 (pending user review).
        - Ollama unavailable: category='unknown', confirmed=0.
    """
    if not transactions:
        return transactions

    corrections = _load_corrections()
    profile     = _load_profile()
    results     = [dict(t) for t in transactions]  # copy — don't mutate input

    # Step 1 — apply user corrections (instant, no LLM)
    results, corrected_count = _apply_corrections(results, corrections)

    # Step 2 — send only uncategorized transactions to Ollama
    # Build an index map so we can write results back at the right position
    needs_llm = [
        (i, t) for i, t in enumerate(results)
        if t.get("category") in (None, "unknown", "")
    ]

    if not needs_llm:
        return results  # everything was handled by corrections

    for batch_start in range(0, len(needs_llm), BATCH_SIZE):
        batch_pairs  = needs_llm[batch_start : batch_start + BATCH_SIZE]
        batch_txns   = [t for _, t in batch_pairs]
        prompt       = build_prompt(batch_txns, profile)

        try:
            print("\n── LLM PROMPT ──────────────────────────────────────────\n")
            print(prompt)
            print("────────────────────────────────────────────────────────\n")
            response = ollama.chat(
                model=OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0},
            )
            raw    = _strip_code_fences(response["message"]["content"])
            parsed = json.loads(raw)

            for item in parsed:
                batch_idx = int(item["index"]) - 1   # 0-based within this batch
                if 0 <= batch_idx < len(batch_pairs):
                    orig_idx = batch_pairs[batch_idx][0]
                    cat = item.get("category", "other").lower().strip()
                    if cat not in CATEGORIES:
                        cat = "other"
                    results[orig_idx]["category"]    = cat
                    results[orig_idx]["subcategory"] = item.get("subcategory") or None
                    results[orig_idx]["confirmed"]   = 0  # LLM guess — not confirmed

        except ollama.ResponseError as e:
            print(f"[categorizer] Ollama error: {e}")
        except ConnectionError:
            print("[categorizer] Ollama is not running — start it with: ollama serve")
        except json.JSONDecodeError as e:
            print(f"[categorizer] Could not parse LLM response as JSON: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"[categorizer] Unexpected error: {e}")

    return results

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
import logging
import re

import ollama

from config import BILLS_FILE, CATEGORIES, CORRECTIONS_FILE, OLLAMA_MODEL, PROFILE_FILE, SUBCATEGORIES

logger = logging.getLogger(__name__)

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


def _load_bill_rules() -> dict:
    """
    Load bills.local.json and extract match_keyword → category rules.
    Only includes bills with a non-empty match_keyword and valid category.
    Lower priority than corrections.json — corrections always win on conflict.
    """
    try:
        bills = json.loads(BILLS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}

    rules = {}
    for bill in bills:
        keyword  = (bill.get("match_keyword") or "").strip()
        category = (bill.get("category") or "").strip()
        if keyword and category in CATEGORIES:
            key = keyword.upper()
            if key not in rules:  # first bill with this keyword wins
                rules[key] = {
                    "category":    category,
                    "subcategory": bill.get("subcategory") or None,
                }
    return rules


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


def _clean_for_llm(description: str) -> str:
    """
    Minimal generic cleanup to make bank description strings more readable
    for the LLM — without hardcoding any merchant names.

    Only structural noise is removed; the words themselves are left as-is
    so the model can attempt its own recognition.

    Rules:
      1. Replace * with space  (payment-processor separator: OPENAI*CHATGPT)
      2. Strip trailing store/branch numbers  (#407, -78, _03, or bare digits)
      3. Insert a space where letters run directly into digits or vice-versa
         (T&TSUPERMARKET stays as-is; CANADA78 → CANADA 78, SDM267 → SDM 267)
      4. Collapse repeated spaces / trim
    """
    desc = description

    # 1. Payment-processor separator
    desc = desc.replace('*', ' ')

    # 2. Strip trailing store/branch numbers
    desc = re.sub(r'#\d+', '', desc)       # #NNN anywhere (e.g. "#21 SURREY" → " SURREY")
    desc = re.sub(r'[-_]\d+$', '', desc)   # trailing -NNN or _NNN
    desc = re.sub(r'\d{2,}$', '', desc)    # trailing bare digit run (e.g. CANADA78 → CANADA)

    # 3. Space between a letter-run and a digit-run
    desc = re.sub(r'([A-Za-z])(\d)', r'\1 \2', desc)
    desc = re.sub(r'(\d)([A-Za-z])', r'\1 \2', desc)

    # 4. Collapse whitespace
    desc = re.sub(r'\s{2,}', ' ', desc).strip()

    return desc


def _build_category_block() -> str:
    """
    Build the category + subcategory reference block for the prompt.

    Format:
        groceries (no subcategory)
        transport → gas | parking | transit | rideshare | car_service | car_repair
    """
    lines = []
    for cat in CATEGORIES:
        subs = SUBCATEGORIES.get(cat, [])
        if subs:
            lines.append(f"  {cat} → {' | '.join(subs)}")
        else:
            lines.append(f"  {cat}")
    return "\n".join(lines)


def build_prompt(transactions: list[dict], profile: str = "") -> str:
    """
    Build the categorization prompt for one batch.

    Only include transactions that don't yet have a category (corrections
    are applied before this is called, so confirmed ones are skipped).
    """
    category_block = _build_category_block()

    txn_lines = []
    for i, t in enumerate(transactions, start=1):
        direction = "spent" if t.get("type") == "debit" else "received"
        readable  = _clean_for_llm(t["description"])
        txn_lines.append(f'{i}. "{readable}" — ${t["amount"]:.2f} ({direction})')
    txn_block = "\n".join(txn_lines)

    profile_section = (
        f"\n\nUser financial profile (use this to improve accuracy):\n{profile}"
        if profile else ""
    )

    return f"""\
You are a personal finance categorizer. Assign each transaction to a category, \
then optionally a subcategory.

Categories and their ONLY allowed subcategories (use null if none fit or the category has none listed):
{category_block}

Rules:
- Reply with ONLY a valid JSON array — no explanation, no markdown, no extra text.
- One object per transaction, in the same order they appear.
- Each object must have:
    "index"      : the transaction number (1-based)
    "category"   : one category from the list above
    "subcategory": one subcategory from that category's → list, or null
- subcategory MUST be null if the category has no → list, or if none of the listed options fit.
- Do NOT invent subcategories. Only use values shown after →.
- If unsure on category, use "other".

Hints:
- "*" separates payment processor from merchant (e.g. "APPLE*MUSIC" → subscriptions, ai_tool or streaming).
- Small recurring amounts ($5–$50) from tech/media companies → subscriptions.
- Large transfers between accounts → transfer.
- "received" transactions → income or transfer, not spending.{profile_section}

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

    bill_rules   = _load_bill_rules()
    corrections  = _load_corrections()
    merged_rules = {**bill_rules, **corrections}  # corrections win on conflict
    profile      = _load_profile()
    results      = [dict(t) for t in transactions]  # copy — don't mutate input

    # Step 1 — apply bill rules + corrections (instant, no LLM)
    results, corrected_count = _apply_corrections(results, merged_rules)

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

        batch_end = min(batch_start + BATCH_SIZE, len(needs_llm))
        logger.debug("Ollama batch %d–%d of %d", batch_start + 1, batch_end, len(needs_llm))
        for i, t in enumerate(batch_txns, start=1):
            readable = _clean_for_llm(t["description"])
            changed  = readable != t["description"]
            suffix   = f"  [cleaned from: {t['description']}]" if changed else ""
            logger.debug("  %2d. %s  $%.2f%s", i, readable, t["amount"], suffix)

        try:
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

                    # Validate subcategory against the allowed list for this category.
                    # If Ollama returns something not in the list, drop it to null.
                    raw_sub    = (item.get("subcategory") or "").lower().strip() or None
                    allowed    = SUBCATEGORIES.get(cat, [])
                    subcategory = raw_sub if (raw_sub and raw_sub in allowed) else None

                    results[orig_idx]["category"]    = cat
                    results[orig_idx]["subcategory"] = subcategory
                    results[orig_idx]["confirmed"]   = 0  # LLM guess — not confirmed

        except ollama.ResponseError as e:
            logger.error("[categorizer] Ollama error: %s", e)
        except ConnectionError:
            logger.error("[categorizer] Ollama is not running — start it with: ollama serve")
        except json.JSONDecodeError as e:
            logger.error("[categorizer] Could not parse LLM response as JSON: %s", e)
        except Exception as e:  # noqa: BLE001
            logger.error("[categorizer] Unexpected error: %s", e)

    return results

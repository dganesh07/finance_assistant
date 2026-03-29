"""
src/reporter.py — Pluggable AI insights agent for the dashboard.

Backends:
  "ollama"  — local Ollama (default, no API key needed)
  "claude"  — Anthropic Claude API (requires ANTHROPIC_API_KEY in config_local.py)

Config (all overridable in config_local.py):
  REPORT_BACKEND    = "ollama" | "claude"
  REPORT_MODEL      = model id string
  ANTHROPIC_API_KEY = "sk-ant-..."   (only for claude backend)

Prompt:
  Loaded from data/prompts/insights_prompt.txt at call time — edit the file to
  tune tone, focus areas, or output format without restarting the server.

Output:
  generate_insights() returns a list of dicts: [{"type": str, "text": str}, ...]
  type is one of: "warning" | "info" | "tip"
"""

import json
import os
import re

from config import (
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    REPORT_BACKEND,
    REPORT_MODEL,
    REPORT_PROMPT_FILE,
)


class ReportError(Exception):
    """Raised when insight generation fails."""


# ── Prompt loader ──────────────────────────────────────────────────────────────

_DEFAULT_SYSTEM_PROMPT = """You are a personal finance analyst. Given a financial context block,
produce 4-6 specific insights as a JSON array. Each element: {"type": "warning"|"info"|"tip", "text": "..."}
Be data-driven — use exact figures from the context. Output only the JSON array, no other text."""


def _load_system_prompt() -> str:
    if REPORT_PROMPT_FILE.exists():
        return REPORT_PROMPT_FILE.read_text(encoding="utf-8").strip()
    return _DEFAULT_SYSTEM_PROMPT


# ── Response parser ────────────────────────────────────────────────────────────

def _parse_insights(text: str) -> list[dict]:
    """
    Extract insight dicts from LLM response text.

    Handles three formats:
      1. JSON array:  [{"type": ..., "text": ...}, ...]
      2. JSON object: {"insights": [...]}   (produced by Ollama format="json")
      3. Plain text:  fallback — each non-empty line becomes an info bullet
    """
    stripped = text.strip()

    # Try full parse first (valid top-level JSON)
    try:
        parsed = json.loads(stripped)
        # Format 2: {"insights": [...]}
        if isinstance(parsed, dict):
            items = parsed.get("insights") or parsed.get("items") or []
            if isinstance(items, list):
                return _items_to_insights(items)
        # Format 1: [...]
        if isinstance(parsed, list):
            return _items_to_insights(parsed)
    except (json.JSONDecodeError, TypeError):
        pass

    # Greedy search for a JSON array anywhere in the text
    match = re.search(r'\[.*\]', stripped, re.DOTALL)
    if match:
        try:
            return _items_to_insights(json.loads(match.group()))
        except (json.JSONDecodeError, TypeError):
            pass

    # Greedy search for a JSON object
    match = re.search(r'\{.*\}', stripped, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group())
            items = parsed.get("insights") or parsed.get("items") or []
            return _items_to_insights(items)
        except (json.JSONDecodeError, TypeError):
            pass

    # Fallback: treat each line as an info bullet
    lines = [
        ln.strip().lstrip("•-*→0123456789.").strip()
        for ln in text.splitlines()
        if ln.strip() and len(ln.strip()) > 10
    ]
    return [{"type": "info", "text": ln} for ln in lines[:6] if ln]


def _items_to_insights(items: list) -> list[dict]:
    out = []
    for item in items:
        if isinstance(item, dict) and "text" in item:
            out.append({
                "type": item.get("type", "info"),
                "text": str(item["text"]).strip(),
            })
    return out


# ── Ollama backend ─────────────────────────────────────────────────────────────

def _call_ollama(system: str, user: str) -> list[dict]:
    try:
        import ollama
    except ImportError as exc:
        raise ReportError("ollama package not installed. Run: pip install ollama") from exc

    try:
        response = ollama.chat(
            model=REPORT_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            format="json",
            options={"temperature": 0},
        )
        # Support both dict-style and object-style access (ollama lib versions differ)
        try:
            content = response["message"]["content"]
        except (TypeError, KeyError):
            content = response.message.content
    except ollama.ResponseError as exc:
        raise ReportError(f"Ollama model error: {exc}") from exc
    except ConnectionError as exc:
        raise ReportError("Ollama is not running — start it with: ollama serve") from exc
    except Exception as exc:
        raise ReportError(f"Ollama call failed: {exc}") from exc

    return _parse_insights(content)


# ── Claude backend ─────────────────────────────────────────────────────────────

def _call_claude(system: str, user: str) -> list[dict]:
    try:
        import anthropic
    except ImportError as exc:
        raise ReportError(
            "anthropic package not installed. Run: pip install anthropic"
        ) from exc

    # API key: env var takes priority, then config_local.py
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        try:
            from config_local import ANTHROPIC_API_KEY  # type: ignore
            api_key = ANTHROPIC_API_KEY
        except (ImportError, AttributeError):
            pass

    if not api_key:
        raise ReportError(
            "ANTHROPIC_API_KEY not set. Add it to config_local.py or set the environment variable."
        )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=REPORT_MODEL,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        content = message.content[0].text
    except Exception as exc:
        raise ReportError(f"Claude API call failed: {exc}") from exc

    return _parse_insights(content)


# ── Public API ─────────────────────────────────────────────────────────────────

def generate_insights(context: str, month: str = "current") -> list[dict]:
    """
    Generate AI insights from a financial context string.

    Args:
        context: Full context string from context_builder.build_context().
        month:   YYYY-MM label for the month being analysed (injected into prompt).

    Returns:
        List of insight dicts: [{"type": "warning"|"info"|"tip", "text": str}, ...]

    Raises:
        ReportError: If the backend call fails or is misconfigured.
    """
    system = _load_system_prompt()
    user   = f"Analyse month: {month}\n\n{context}"

    sep = "─" * 60
    print(f"\n{sep}")
    print(f"[reporter] backend={REPORT_BACKEND}  model={REPORT_MODEL}")
    print(f"{sep}")
    print("[reporter] SYSTEM PROMPT:")
    print(system)
    print(f"{sep}")
    print("[reporter] USER MESSAGE:")
    print(user)
    print(f"{sep}\n")

    if REPORT_BACKEND == "claude":
        return _call_claude(system, user)
    else:
        return _call_ollama(system, user)

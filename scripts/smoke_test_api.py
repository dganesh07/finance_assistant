#!/usr/bin/env python3
"""
scripts/smoke_test_api.py — Transaction API smoke test.

Hits the live FastAPI server (port 8000) and runs a quick round-trip
over the transaction CRUD endpoints. Uses only test data — no real
statements are touched and all inserts use a recognisable description
so they can be spotted / ignored.

Run with the server already started (./dev.sh or uvicorn api:app):
    python scripts/smoke_test_api.py
    python scripts/smoke_test_api.py --base http://localhost:8000
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import requests
except ImportError:
    print("requests not installed — run: pip install requests")
    sys.exit(1)

try:
    from rich.console import Console
    from rich import box
    from rich.table import Table
    RICH = True
except ImportError:
    RICH = False

# ── Helpers ────────────────────────────────────────────────────────────────────

console = Console() if RICH else None

PASS = "[bold green]✓[/bold green]" if RICH else "✓"
FAIL = "[bold red]✗[/bold red]"   if RICH else "✗"

_results: list[tuple[bool, str]] = []


def _p(msg: str) -> None:
    if RICH:
        console.print(msg)
    else:
        # Strip rich markup for plain output
        import re
        print(re.sub(r"\[/?[^\]]+\]", "", msg))


def check(label: str, condition: bool, detail: str = "") -> bool:
    mark = PASS if condition else FAIL
    suffix = f"  [dim]{detail}[/dim]" if detail and RICH else (f"  {detail}" if detail else "")
    _p(f"  {mark}  {label}{suffix}")
    _results.append((condition, label))
    return condition


# ── Smoke phases ───────────────────────────────────────────────────────────────

def phase(title: str) -> None:
    _p(f"\n[bold cyan]{'─'*56}[/bold cyan]")
    _p(f"[bold white]  {title}[/bold white]")
    _p(f"[bold cyan]{'─'*56}[/bold cyan]")


def run(base: str) -> bool:
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})

    # ── ① Reachability ─────────────────────────────────────────────────────────
    phase("① REACHABILITY")
    try:
        r = s.get(f"{base}/api/categories", timeout=5)
        ok = r.status_code == 200 and isinstance(r.json(), list)
        check("GET /api/categories returns 200 + list", ok, f"status={r.status_code}")
    except requests.exceptions.ConnectionError:
        _p(f"  {FAIL}  Cannot reach {base} — is the server running?")
        return False

    # ── ② Create transaction ───────────────────────────────────────────────────
    phase("② CREATE TRANSACTION")
    payload = {
        "date":        "2099-01-15",   # far future — won't collide with real data
        "description": "SMOKE_TEST_TXN",
        "amount":      42.00,
        "type":        "debit",
        "account":     "td_chequing",
        "category":    "other",
        "confirmed":   0,
    }
    r = s.post(f"{base}/api/transactions", json=payload)
    created_ok = r.status_code == 200
    check("POST /api/transactions returns 200", created_ok, f"status={r.status_code}")
    if not created_ok:
        _p("  [red]Cannot continue without a created transaction.[/red]")
        return False

    txn = r.json()
    txn_id = txn["id"]
    check("Response has id, date, amount", all(k in txn for k in ("id", "date", "amount")))
    check("Amount round-tripped correctly", txn["amount"] == 42.0, f"got {txn['amount']}")
    check("confirmed=0 as sent",           txn["confirmed"] == 0)

    # ── ③ Read back via list endpoint ─────────────────────────────────────────
    phase("③ READ BACK")
    r = s.get(f"{base}/api/transactions?search=SMOKE_TEST_TXN&limit=10")
    check("GET /api/transactions?search= returns 200", r.status_code == 200)
    results = r.json().get("transactions", [])
    found = any(t["id"] == txn_id for t in results)
    check("Created transaction appears in search results", found)

    # ── ④ Filter by date range ─────────────────────────────────────────────────
    phase("④ DATE RANGE FILTER")
    r_in  = s.get(f"{base}/api/transactions?date_from=2099-01-01&date_to=2099-01-31")
    r_out = s.get(f"{base}/api/transactions?date_from=2099-02-01&date_to=2099-02-28")
    check("date_from/to includes 2099-01-15",   r_in.status_code == 200  and any(t["id"] == txn_id for t in r_in.json().get("transactions", [])))
    check("date_from/to excludes 2099-01-15",   r_out.status_code == 200 and not any(t["id"] == txn_id for t in r_out.json().get("transactions", [])))

    # ── ⑤ Patch category ──────────────────────────────────────────────────────
    phase("⑤ PATCH CATEGORY")
    r = s.patch(f"{base}/api/transactions/{txn_id}", json={"category": "groceries"})
    check("PATCH /api/transactions/{id} returns 200", r.status_code == 200, f"status={r.status_code}")
    check("Category updated in response",            r.json().get("category") == "groceries")

    # ── ⑥ Patch notes ─────────────────────────────────────────────────────────
    phase("⑥ PATCH NOTES")
    r = s.patch(f"{base}/api/transactions/{txn_id}", json={"notes": "smoke test note"})
    check("Notes saved",  r.status_code == 200 and r.json().get("notes") == "smoke test note")
    r = s.patch(f"{base}/api/transactions/{txn_id}", json={"notes": None})
    check("Notes cleared (null)", r.status_code == 200 and r.json().get("notes") is None)

    # ── ⑦ Confirm ─────────────────────────────────────────────────────────────
    phase("⑦ CONFIRM")
    r = s.patch(f"{base}/api/transactions/{txn_id}", json={"confirmed": 1})
    check("PATCH confirmed=1 returns 200", r.status_code == 200)
    check("confirmed=1 in response",       r.json().get("confirmed") == 1)

    # Verify not in /review anymore
    r = s.get(f"{base}/api/transactions/review")
    review_ids = {t["id"] for t in r.json()} if r.status_code == 200 else set()
    check("No longer in /review after confirm", txn_id not in review_ids)

    # ── ⑧ One-time toggle ─────────────────────────────────────────────────────
    phase("⑧ ONE-TIME TOGGLE")
    r = s.patch(f"{base}/api/transactions/{txn_id}", json={"is_one_time": 1})
    check("Mark is_one_time=1", r.status_code == 200 and r.json().get("is_one_time") == 1)
    r = s.patch(f"{base}/api/transactions/{txn_id}", json={"is_one_time": 0})
    check("Unmark is_one_time=0", r.status_code == 200 and r.json().get("is_one_time") == 0)

    # ── ⑨ confirm-all ──────────────────────────────────────────────────────────
    phase("⑨ CONFIRM-ALL")
    # Create two unconfirmed transactions, bulk-confirm them
    ids = []
    for i in range(2):
        r = s.post(f"{base}/api/transactions", json={
            "date": f"2099-0{i+2}-01", "description": f"SMOKE_BATCH_{i}",
            "amount": 10.0, "confirmed": 0,
        })
        if r.status_code == 200:
            ids.append(r.json()["id"])
    r = s.post(f"{base}/api/transactions/confirm-all", json={"ids": ids})
    check("POST /api/transactions/confirm-all returns 200", r.status_code == 200)
    check(f"updated={len(ids)} reported",  r.json().get("updated") == len(ids), f"got {r.json().get('updated')}")

    # ── ⑩ Error paths ─────────────────────────────────────────────────────────
    phase("⑩ ERROR PATHS")
    r404  = s.patch(f"{base}/api/transactions/999999999", json={"category": "groceries"})
    r400  = s.patch(f"{base}/api/transactions/{txn_id}",  json={"category": "not_a_real_cat"})
    check("PATCH unknown id → 404",        r404.status_code == 404)
    check("PATCH invalid category → 400",  r400.status_code == 400)

    # ── Verdict ────────────────────────────────────────────────────────────────
    passed = sum(1 for ok, _ in _results if ok)
    total  = len(_results)
    all_ok = passed == total

    _p("")
    if all_ok:
        _p(f"[bold green]ALL {total} CHECKS PASSED ✓[/bold green]")
    else:
        failed_labels = [lbl for ok, lbl in _results if not ok]
        _p(f"[bold red]{total - passed}/{total} CHECKS FAILED ✗[/bold red]")
        for lbl in failed_labels:
            _p(f"  [red]✗  {lbl}[/red]")

    return all_ok


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transaction API smoke test — requires a running server.",
        epilog="Start the server first: ./dev.sh  (or  uvicorn api:app --reload)",
    )
    parser.add_argument("--base", default="http://localhost:8000",
                        help="API base URL (default: http://localhost:8000)")
    args = parser.parse_args()

    _p(f"\n[bold]Finance Agent — Transaction API Smoke Test[/bold]")
    _p(f"[dim]Target: {args.base}[/dim]")

    ok = run(args.base)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

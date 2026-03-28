"""
api.py — FastAPI backend for the Finance Agent dashboard.

Run:
  uvicorn api:app --reload --port 8000

Endpoints:
  GET  /api/summary                       — spending summary + runway
  GET  /api/transactions                  — all transactions (filterable)
  GET  /api/transactions/review           — unconfirmed transactions only
  PATCH /api/transactions/{id}            — update category / confirm a row
  POST /api/transactions/confirm-all      — confirm multiple transactions by IDs
  GET  /api/bills                         — bills from bills.json
  POST /api/apply-corrections             — apply corrections.json to all unknowns (fast, no LLM)
  POST /api/run-categorizer               — trigger Ollama categorizer in background
  GET  /api/job/{job_id}                  — poll background job status
  GET  /api/categories                    — full category list from config
  GET  /api/subcategories                 — subcategory map { category: [subcategory, ...] }
  GET  /api/monthly                       — per-month breakdown with completeness flags
  GET  /api/monthly-subcategories?month=  — subcategory drill-down for a specific month
  GET  /api/spending-periods              — all months with is_complete + statement date ranges
  POST /api/parse-statements              — scan statements folder, save raw rows to DB
  GET  /api/statements                    — list files in data/statements/
  GET  /api/corrections                   — view all rules in corrections.json
  POST /api/corrections                   — add/update a correction rule
  DELETE /api/corrections/{key}           — remove a correction rule
  GET  /api/context                       — full LLM-ready financial context block
"""

import json
import sqlite3
import uuid
from datetime import date, timedelta
from typing import List, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import (
    BILLS_FILE, BURN_RATE_START, CATEGORIES, CORRECTIONS_FILE, DB_PATH,
    FIXED_CATEGORIES, REPORT_BACKEND, REPORT_MODEL, STATEMENTS_DIR, SUBCATEGORIES,
)
from src.categorizer import categorize_transactions
from src.context_builder import build_context
from src.parser import parse_new_statements

app = FastAPI(title="Finance Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── DB helper ──────────────────────────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    """Open a SQLite connection to DB_PATH with Row factory enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── In-memory job tracker (background categorizer runs) ───────────────────────

_jobs: dict[str, dict] = {}


# ── Request models ─────────────────────────────────────────────────────────────

class TransactionUpdate(BaseModel):
    category:    Optional[str] = None
    subcategory: Optional[str] = None
    confirmed:   Optional[int] = None
    is_one_time: Optional[int] = None
    notes:       Optional[str] = None


class CorrectionRule(BaseModel):
    key:        str           # substring to match (stored uppercase)
    category:   str
    subcategory: Optional[str] = None


class ConfirmAllRequest(BaseModel):
    ids: list[int]


# ── /api/categories ────────────────────────────────────────────────────────────

@app.get("/api/categories")
def get_categories():
    return CATEGORIES


@app.get("/api/subcategories")
def get_subcategories():
    """Return the canonical subcategory map: { category: [subcategory, ...] }"""
    return SUBCATEGORIES


# ── /api/bills ─────────────────────────────────────────────────────────────────

@app.get("/api/bills")
def get_bills():
    try:
        return json.loads(BILLS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []


# ── /api/summary ───────────────────────────────────────────────────────────────

@app.get("/api/summary")
def get_summary(days: int = 60):
    """
    Spending summary for the last N days.

    - total_in / total_out exclude transfers and fee rebates (same logic as check_db)
    - runway_months = total_in / avg_monthly_spend (rough estimate)
    - review_count = transactions with confirmed=0
    """
    period_start = (date.today() - timedelta(days=days)).isoformat()
    conn = get_conn()

    row = conn.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN type='credit' AND category NOT IN ('transfer','fees','refund')
                              THEN amount ELSE 0 END), 0) AS total_in,
            COALESCE(SUM(CASE WHEN type='debit'  AND category NOT IN ('transfer','fees')
                              THEN amount ELSE 0 END), 0)
            - COALESCE(SUM(CASE WHEN type='credit' AND category = 'refund'
                              THEN amount ELSE 0 END), 0) AS total_out
        FROM transactions
        WHERE date >= ?
    """, (period_start,)).fetchone()

    total_in  = row["total_in"]
    total_out = row["total_out"]
    net       = total_in - total_out

    cats = conn.execute("""
        SELECT category,
               COALESCE(SUM(CASE WHEN type='debit' THEN amount ELSE -amount END), 0) AS total,
               COUNT(*) AS count
        FROM transactions
        WHERE date >= ?
          AND category NOT IN ('transfer', 'fees')
          AND (type = 'debit' OR category = 'refund')
        GROUP BY category
        ORDER BY total DESC
    """, (period_start,)).fetchall()

    avg_monthly   = (total_out / days) * 30 if days > 0 and total_out > 0 else 0
    runway_months = round(total_in / avg_monthly, 1) if avg_monthly > 0 else None

    review_count = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE confirmed = 0"
    ).fetchone()[0]

    conn.close()
    return {
        "period":        f"last {days} days",
        "period_start":  period_start,
        "total_in":      round(total_in, 2),
        "total_out":     round(total_out, 2),
        "net":           round(net, 2),
        "runway_months": runway_months,
        "review_count":  review_count,
        "by_category": [
            {"category": r["category"], "total": round(r["total"], 2), "count": r["count"]}
            for r in cats
        ],
    }


# ── /api/transactions/review ───────────────────────────────────────────────────

@app.get("/api/transactions/review")
def get_review_transactions():
    """All unconfirmed transactions, newest first."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT id, date, description, amount, type, account,
               category, subcategory, confirmed, source_file, notes, is_one_time
        FROM transactions
        WHERE confirmed = 0
        ORDER BY date DESC, id DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── /api/transactions ──────────────────────────────────────────────────────────

@app.get("/api/transactions")
def get_transactions(
    category:    Optional[str] = None,
    confirmed:   Optional[int] = None,
    search:      Optional[str] = None,
    date_from:   Optional[str] = None,
    date_to:     Optional[str] = None,
    source_file: Optional[List[str]] = Query(default=None),
    limit:       int = 100,
    offset:      int = 0,
):
    """All transactions with optional filters. Returns total count and paginated rows."""
    conn = get_conn()

    clauses, params = [], []

    if category:
        clauses.append("category = ?")
        params.append(category)
    if confirmed is not None:
        clauses.append("confirmed = ?")
        params.append(confirmed)
    if search:
        clauses.append("description LIKE ?")
        params.append(f"%{search}%")
    if date_from:
        clauses.append("date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("date <= ?")
        params.append(date_to)
    if source_file:
        placeholders = ",".join("?" * len(source_file))
        clauses.append(f"source_file IN ({placeholders})")
        params.extend(source_file)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    rows = conn.execute(
        f"SELECT id, date, description, amount, type, account, "
        f"category, subcategory, confirmed, source_file, notes, is_one_time "
        f"FROM transactions {where} ORDER BY date DESC, id DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    total = conn.execute(
        f"SELECT COUNT(*) FROM transactions {where}", params
    ).fetchone()[0]

    conn.close()
    return {"total": total, "transactions": [dict(r) for r in rows]}


# ── PATCH /api/transactions/{id} ───────────────────────────────────────────────

@app.patch("/api/transactions/{txn_id}")
def update_transaction(txn_id: int, body: TransactionUpdate):
    """Update category, subcategory, confirmed, is_one_time, or notes on a single transaction."""
    # "unknown" is a valid DB placeholder (uncategorized), not user-selectable
    # but should never trigger a 400 when confirming without changing category.
    _ALLOWED = set(CATEGORIES) | {"unknown"}
    if body.category and body.category not in _ALLOWED:
        raise HTTPException(400, f"Invalid category '{body.category}'. "
                                 f"Valid: {', '.join(CATEGORIES)}")

    conn = get_conn()

    if not conn.execute("SELECT id FROM transactions WHERE id = ?", (txn_id,)).fetchone():
        conn.close()
        raise HTTPException(404, "Transaction not found")

    fields, params = [], []
    if body.category    is not None: fields.append("category = ?");    params.append(body.category)
    # subcategory uses model_fields_set so an explicit null clears the value in DB
    if "subcategory" in body.model_fields_set:
        fields.append("subcategory = ?")
        params.append(body.subcategory or None)   # empty string also clears
    if body.confirmed   is not None: fields.append("confirmed = ?");   params.append(body.confirmed)
    if body.is_one_time is not None: fields.append("is_one_time = ?"); params.append(body.is_one_time)
    if body.notes       is not None: fields.append("notes = ?");       params.append(body.notes)

    if fields:
        conn.execute(
            f"UPDATE transactions SET {', '.join(fields)} WHERE id = ?",
            params + [txn_id],
        )
        conn.commit()

    row = conn.execute(
        "SELECT id, date, description, amount, type, account, "
        "category, subcategory, confirmed, notes FROM transactions WHERE id = ?",
        (txn_id,),
    ).fetchone()
    conn.close()
    return dict(row)


# ── POST /api/transactions/confirm-all ────────────────────────────────────────

@app.post("/api/transactions/confirm-all")
def confirm_all(body: ConfirmAllRequest):
    """Bulk-confirm a list of transaction IDs (set confirmed=1)."""
    if not body.ids:
        return {"updated": 0}
    conn = get_conn()
    conn.executemany(
        "UPDATE transactions SET confirmed = 1 WHERE id = ?",
        [(i,) for i in body.ids],
    )
    conn.commit()
    conn.close()
    return {"updated": len(body.ids)}



# ── POST /api/run-categorizer ─────────────────────────────────────────────────

def _run_categorizer_job(job_id: str) -> None:
    """
    Background task — fetches all unknown/unconfirmed transactions,
    sends them to the Ollama categorizer, writes results back to DB.
    """
    _jobs[job_id] = {"status": "running", "processed": 0, "categorized": 0}
    try:
        conn = get_conn()
        rows = conn.execute("""
            SELECT id, date, description, amount, type, account
            FROM transactions
            WHERE (category IS NULL OR category IN ('unknown', ''))
              AND confirmed = 0
        """).fetchall()

        txns = [dict(r) for r in rows]
        if not txns:
            _jobs[job_id] = {"status": "done", "processed": 0, "categorized": 0}
            conn.close()
            return

        results     = categorize_transactions(txns)
        categorized = 0
        for orig, result in zip(txns, results):
            cat = result.get("category", "unknown")
            sub = result.get("subcategory")
            if cat and cat != "unknown":
                conn.execute(
                    "UPDATE transactions SET category = ?, subcategory = ? WHERE id = ?",
                    (cat, sub, orig["id"]),
                )
                categorized += 1
        conn.commit()
        conn.close()
        _jobs[job_id] = {"status": "done", "processed": len(txns), "categorized": categorized}

    except Exception as e:
        _jobs[job_id] = {"status": "error", "error": str(e)}


@app.post("/api/run-categorizer")
def run_categorizer(background_tasks: BackgroundTasks):
    """Kick off a background Ollama categorization job for all unknown transactions."""
    job_id = str(uuid.uuid4())[:8]
    background_tasks.add_task(_run_categorizer_job, job_id)
    return {"job_id": job_id, "status": "started"}


# ── POST /api/apply-corrections ────────────────────────────────────────────────

@app.post("/api/apply-corrections")
def apply_corrections_endpoint():
    """
    Apply corrections.json to every transaction that is still 'unknown'.
    Also backfills subcategories onto already-confirmed rows that have none.

    Fast — no LLM involved. Returns immediately with counts.
    Use this after saving new rules in the Review UI so newly-added corrections
    are stamped across all existing unconfirmed transactions instantly.
    """
    from src.categorizer import _load_corrections, _load_bill_rules, _apply_corrections

    conn         = get_conn()
    bill_rules   = _load_bill_rules()
    corrections  = _load_corrections()
    merged_rules = {**bill_rules, **corrections}  # corrections win on conflict

    # ── Pass 1: categorize unknown/unconfirmed rows ─────────────────────────────
    rows = conn.execute("""
        SELECT id, description, amount, type, category
        FROM transactions
        WHERE (category IS NULL OR category IN ('unknown', '')) AND confirmed = 0
    """).fetchall()

    applied = 0
    if rows:
        txns             = [dict(r) for r in rows]
        stamped, applied = _apply_corrections(txns, merged_rules)
        for result in stamped:
            if result.get("category") not in (None, "unknown", ""):
                conn.execute(
                    "UPDATE transactions SET category = ?, subcategory = ?, confirmed = ? WHERE id = ?",
                    (result["category"], result.get("subcategory"), result.get("confirmed", 1), result["id"]),
                )

    # ── Pass 2: backfill missing subcategories on confirmed rows ────────────────
    # A confirmed transaction may have been categorised before subcategory rules
    # existed.  If a rule now supplies a subcategory AND the category still
    # matches, fill in the gap without touching the confirmed flag.
    confirmed_no_sub = conn.execute("""
        SELECT id, description, category
        FROM transactions
        WHERE confirmed = 1 AND (subcategory IS NULL OR subcategory = '')
    """).fetchall()

    backfilled = 0
    for row in confirmed_no_sub:
        desc_upper = row["description"].upper()
        for key, override in merged_rules.items():
            sub = override.get("subcategory")
            if (
                sub
                and key.upper() in desc_upper
                and override.get("category") == row["category"]
            ):
                conn.execute(
                    "UPDATE transactions SET subcategory = ? WHERE id = ?",
                    (sub, row["id"]),
                )
                backfilled += 1
                break  # first matching rule wins

    conn.commit()
    remaining = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE (category IS NULL OR category IN ('unknown','')) AND confirmed = 0"
    ).fetchone()[0]
    conn.close()

    return {"applied": applied, "backfilled_subcategories": backfilled, "remaining_unknown": remaining}


# ── GET /api/monthly ───────────────────────────────────────────────────────────

@app.get("/api/monthly")
def get_monthly(months: int = 6):
    """
    Per-month spending breakdown for the last N months that have transactions.

    Returns newest month first. Each month includes:
      - total_out  : sum of debits (excl. transfer, fees, investment)
      - total_in   : sum of income credits (excl. transfer, fees, refund)
      - refunds    : sum of refund credits (reduces net spend)
      - net        : total_in - (total_out - refunds)
      - by_category: list of { category, total, count } sorted by total desc
    """
    conn = get_conn()

    month_rows = conn.execute("""
        SELECT DISTINCT strftime('%Y-%m', date) AS month
        FROM transactions
        ORDER BY month DESC
        LIMIT ?
    """, (months,)).fetchall()

    result = []
    for row in month_rows:
        month = row["month"]
        y, m   = map(int, month.split("-"))
        m_start = f"{month}-01"
        m_end   = f"{y}-{m+1:02d}-01" if m < 12 else f"{y+1}-01-01"

        # Regular spend (recurring — used for burn rate)
        regular_rows = conn.execute("""
            SELECT category,
                   COALESCE(SUM(amount), 0) AS total,
                   COUNT(*) AS count
            FROM transactions
            WHERE date >= ? AND date < ?
              AND type = 'debit'
              AND (is_one_time = 0 OR is_one_time IS NULL)
              AND category NOT IN ('transfer', 'fees', 'investment')
            GROUP BY category
            ORDER BY total DESC
        """, (m_start, m_end)).fetchall()

        # One-time spend (flagged items — excluded from burn rate)
        one_time_rows = conn.execute("""
            SELECT category, description,
                   COALESCE(SUM(amount), 0) AS total,
                   COUNT(*) AS count
            FROM transactions
            WHERE date >= ? AND date < ?
              AND type = 'debit'
              AND is_one_time = 1
              AND category NOT IN ('transfer', 'fees', 'investment')
            GROUP BY category, description
            ORDER BY total DESC
        """, (m_start, m_end)).fetchall()

        total_in = conn.execute("""
            SELECT COALESCE(SUM(amount), 0) AS t
            FROM transactions
            WHERE date >= ? AND date < ?
              AND type = 'credit'
              AND category NOT IN ('transfer', 'fees', 'refund')
        """, (m_start, m_end)).fetchone()["t"]

        refunds = conn.execute("""
            SELECT COALESCE(SUM(amount), 0) AS t
            FROM transactions
            WHERE date >= ? AND date < ?
              AND type = 'credit' AND category = 'refund'
        """, (m_start, m_end)).fetchone()["t"]

        regular_out  = sum(r["total"] for r in regular_rows)
        one_time_out = sum(r["total"] for r in one_time_rows)
        total_out    = regular_out + one_time_out

        # Per-account statement coverage — read directly from account_balances.
        # covers_month is computed and stored by the parser (upsert_spending_periods).
        acct_rows = conn.execute("""
            SELECT account, statement_start, statement_end, covers_month
            FROM account_balances
            WHERE statement_month = ?
            ORDER BY account
        """, (month,)).fetchall()

        accounts_covered = [
            {
                "account":         r["account"],
                "statement_start": r["statement_start"],
                "statement_end":   r["statement_end"],
                "covers_month":    bool(r["covers_month"]),
            }
            for r in acct_rows
        ]

        result.append({
            "label":            month,
            "accounts_covered": accounts_covered,
            "total_out":    round(total_out,    2),
            "regular_out":  round(regular_out,  2),
            "one_time_out": round(one_time_out, 2),
            "total_in":     round(total_in,     2),
            "refunds":      round(refunds,      2),
            "net":          round(total_in - (total_out - refunds), 2),
            "by_category":  [
                {"category": r["category"], "total": round(r["total"], 2), "count": r["count"]}
                for r in regular_rows
            ],
            "one_time_items": [
                {"category": r["category"], "description": r["description"],
                 "total": round(r["total"], 2), "count": r["count"]}
                for r in one_time_rows
            ],
        })

    conn.close()
    return {"months": result}


# ── GET /api/monthly-subcategories ─────────────────────────────────────────────

@app.get("/api/monthly-subcategories")
def get_monthly_subcategories(month: str):
    """
    Subcategory breakdown for a specific calendar month (YYYY-MM).

    Returns a flat list of { category, subcategory, total, count } covering all
    debits in that month, excluding transfer/fees/investment.  subcategory is
    null for transactions that have no subcategory assigned.

    Designed for drill-down charts: group by category on the frontend, then
    expand to subcategory on click.  Also included in the report agent context.
    """
    try:
        y, m = map(int, month.split("-"))
    except (ValueError, AttributeError):
        raise HTTPException(400, "month must be YYYY-MM")

    m_start = f"{month}-01"
    m_end   = f"{y}-{m+1:02d}-01" if m < 12 else f"{y+1}-01-01"

    conn = get_conn()
    rows = conn.execute("""
        SELECT category,
               subcategory,
               COALESCE(SUM(amount), 0) AS total,
               COUNT(*) AS count
        FROM transactions
        WHERE date >= ? AND date < ?
          AND type = 'debit'
          AND category NOT IN ('transfer', 'fees', 'investment')
        GROUP BY category, subcategory
        ORDER BY category, total DESC
    """, (m_start, m_end)).fetchall()
    conn.close()

    return [
        {
            "category":    r["category"],
            "subcategory": r["subcategory"],   # may be None
            "total":       round(r["total"], 2),
            "count":       r["count"],
        }
        for r in rows
    ]


# ── GET /api/spending-periods ──────────────────────────────────────────────────

@app.get("/api/spending-periods")
def get_spending_periods():
    """
    All calendar months that have transactions, with per-account coverage detail.

    Each month returns an `accounts` array — one entry per account that has a
    statement imported for that month, with the official statement start/end dates
    and whether that account's statement fully covers the calendar month.

    The top-level `is_complete` flag (from spending_periods) is retained as a
    fallback signal but the `accounts` array is the authoritative source for the
    month-picker UI: show what's there, let the user judge completeness themselves.

    Example response item:
      {
        "period_label": "2026-02",
        "is_baseline":  1,
        "accounts": [
          { "account": "chequing",   "statement_start": "2026-01-30",
            "statement_end": "2026-02-27", "covers_month": false },
          { "account": "creditcard", "statement_start": "2026-01-28",
            "statement_end": "2026-02-27", "covers_month": false }
        ]
      }
    """
    import calendar as _cal
    conn = get_conn()

    periods = conn.execute("""
        SELECT period_label, is_baseline
        FROM spending_periods
        ORDER BY period_label DESC
    """).fetchall()

    result = []
    for period in periods:
        month = period["period_label"]

        acct_rows = conn.execute("""
            SELECT account, statement_start, statement_end, covers_month
            FROM account_balances
            WHERE statement_month = ?
            ORDER BY account
        """, (month,)).fetchall()

        result.append({
            "period_label": month,
            "is_baseline":  period["is_baseline"],
            "accounts": [
                {
                    "account":         r["account"],
                    "statement_start": r["statement_start"],
                    "statement_end":   r["statement_end"],
                    "covers_month":    bool(r["covers_month"]),
                }
                for r in acct_rows
            ],
        })

    conn.close()
    return result


# ── GET /api/job/{job_id} ──────────────────────────────────────────────────────

@app.get("/api/job/{job_id}")
def get_job(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")
    return _jobs[job_id]


# ── GET /api/source-files ──────────────────────────────────────────────────────

@app.get("/api/source-files")
def get_source_files():
    """Distinct source files in the DB, ordered by most recently imported."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT source_file, MAX(created_at) AS latest, COUNT(*) AS count
        FROM transactions
        WHERE source_file IS NOT NULL
        GROUP BY source_file
        ORDER BY latest DESC
        LIMIT 20
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── GET /api/statements ────────────────────────────────────────────────────────

@app.get("/api/statements")
def list_statements():
    """List PDF/CSV files in data/statements/ so the UI can show what's available."""
    files = sorted(
        p.name for p in STATEMENTS_DIR.glob("*")
        if p.suffix.lower() in (".pdf", ".csv")
    )
    return {"files": files, "directory": str(STATEMENTS_DIR)}


# ── POST /api/parse-statements ─────────────────────────────────────────────────

@app.post("/api/parse-statements")
def parse_statements():
    """
    Scan data/statements/ for any files not yet imported, parse them,
    and save raw transactions to the DB (category='unknown', confirmed=0).

    This is the UI equivalent of running `python run.py`.
    Already-imported files are skipped automatically (hash dedup).

    After this, call POST /api/run-categorizer to fill in categories,
    then review them in the Review page.
    """
    results = parse_new_statements()

    total_inserted = sum(r.get("inserted", 0) for r in results)
    total_skipped  = sum(r.get("skipped",  0) for r in results)
    total_dropped  = sum(r.get("dropped",  0) for r in results)
    total_outliers = sum(len(r.get("outlier_warnings", [])) for r in results)

    return {
        "files_processed":  len(results),
        "total_inserted":   total_inserted,
        "total_skipped":    total_skipped,
        "total_dropped":    total_dropped,
        "total_outliers":   total_outliers,
        "files": [
            {
                "file":             r["file"],
                "inserted":         r.get("inserted", 0),
                "skipped":          r.get("skipped",  0),
                "dropped":          r.get("dropped",  0),
                "outlier_warnings": r.get("outlier_warnings", []),
                "reconciliation":   r.get("reconciliation"),
            }
            for r in results
        ],
    }


# ── GET /api/context ───────────────────────────────────────────────────────────

@app.get("/api/context")
def get_context():
    """
    Returns the full financial context block that will be fed to the AI report/chat agent.
    Use this to inspect and verify what the AI sees before running a report.
    """
    text = build_context()
    return {"context": text}


# ── GET /api/dashboard ─────────────────────────────────────────────────────────

@app.get("/api/dashboard")
def get_dashboard(month: Optional[str] = None):
    """
    All data needed for the hybrid monthly dashboard.

    Defaults to the most recent month with transactions.
    Returns: stat cards with prev-month deltas, by_category with subcategories,
    fixed/variable split, bills, one-time charges, available month list.
    """
    conn = get_conn()

    # ── Available months ────────────────────────────────────────────────────────
    month_rows = conn.execute("""
        SELECT DISTINCT strftime('%Y-%m', date) AS m
        FROM transactions
        ORDER BY m DESC
        LIMIT 24
    """).fetchall()
    available = [r["m"] for r in month_rows]

    if not available:
        conn.close()
        return {"available_months": [], "month": None}

    target = month if month in available else available[0]
    y, m_num = map(int, target.split("-"))
    m_start  = f"{target}-01"
    m_end    = f"{y}-{m_num+1:02d}-01" if m_num < 12 else f"{y+1}-01-01"

    # Previous month
    prev_m_num = m_num - 1 if m_num > 1 else 12
    prev_y     = y if m_num > 1 else y - 1
    prev_label = f"{prev_y}-{prev_m_num:02d}"
    prev_start = f"{prev_label}-01"
    prev_end   = m_start  # current month start = prev month end

    # ── Current month: income, refunds ─────────────────────────────────────────
    totals = conn.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN type='debit'
                              AND category NOT IN ('transfer','fees','investment')
                              AND (is_one_time=0 OR is_one_time IS NULL)
                              THEN amount ELSE 0 END), 0) AS regular_out,
            COALESCE(SUM(CASE WHEN type='debit'
                              AND category NOT IN ('transfer','fees','investment')
                              AND is_one_time=1
                              THEN amount ELSE 0 END), 0) AS one_time_out,
            COALESCE(SUM(CASE WHEN type='credit'
                              AND category NOT IN ('transfer','fees','refund')
                              THEN amount ELSE 0 END), 0) AS income,
            COALESCE(SUM(CASE WHEN type='credit' AND category='refund'
                              THEN amount ELSE 0 END), 0) AS refunds
        FROM transactions WHERE date >= ? AND date < ?
    """, (m_start, m_end)).fetchone()

    spent    = round(totals["regular_out"] + totals["one_time_out"], 2)
    income   = round(totals["income"], 2)
    refunds  = round(totals["refunds"], 2)
    net      = round(income - (spent - refunds), 2)

    txn_count = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE date >= ? AND date < ?",
        (m_start, m_end)
    ).fetchone()[0]

    # ── Previous month totals (for deltas) ─────────────────────────────────────
    prev_totals = conn.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN type='debit'
                              AND category NOT IN ('transfer','fees','investment')
                              THEN amount ELSE 0 END), 0) AS spent,
            COALESCE(SUM(CASE WHEN type='credit'
                              AND category NOT IN ('transfer','fees','refund')
                              THEN amount ELSE 0 END), 0) AS income
        FROM transactions WHERE date >= ? AND date < ?
    """, (prev_start, prev_end)).fetchone()

    # ── Categories with subcategories for current month ────────────────────────
    cat_rows = conn.execute("""
        SELECT category,
               COALESCE(SUM(amount), 0) AS total,
               COUNT(*)                 AS count
        FROM transactions
        WHERE date >= ? AND date < ?
          AND type = 'debit'
          AND category NOT IN ('transfer','fees','investment')
          AND (is_one_time = 0 OR is_one_time IS NULL)
        GROUP BY category
        ORDER BY total DESC
    """, (m_start, m_end)).fetchall()

    subcat_rows = conn.execute("""
        SELECT category, subcategory,
               COALESCE(SUM(amount), 0) AS total,
               COUNT(*)                 AS count
        FROM transactions
        WHERE date >= ? AND date < ?
          AND type = 'debit'
          AND category NOT IN ('transfer','fees','investment')
          AND (is_one_time = 0 OR is_one_time IS NULL)
        GROUP BY category, subcategory
        ORDER BY category, total DESC
    """, (m_start, m_end)).fetchall()

    # Build subcategory lookup
    subcat_map: dict[str, list] = {}
    for r in subcat_rows:
        cat = r["category"]
        subcat_map.setdefault(cat, []).append({
            "subcategory": r["subcategory"],
            "total":       round(r["total"], 2),
            "count":       r["count"],
        })

    # Previous month per-category totals for delta
    prev_cat_rows = conn.execute("""
        SELECT category, COALESCE(SUM(amount), 0) AS total
        FROM transactions
        WHERE date >= ? AND date < ?
          AND type = 'debit'
          AND category NOT IN ('transfer','fees','investment')
        GROUP BY category
    """, (prev_start, prev_end)).fetchall()
    prev_cat_lookup = {r["category"]: round(r["total"], 2) for r in prev_cat_rows}

    categories = [
        {
            "category":    r["category"],
            "total":       round(r["total"], 2),
            "count":       r["count"],
            "prev_total":  prev_cat_lookup.get(r["category"], 0.0),
            "subcategories": subcat_map.get(r["category"], []),
        }
        for r in cat_rows
    ]

    # ── Fixed vs Variable ───────────────────────────────────────────────────────
    fixed_total    = round(sum(c["total"] for c in categories if c["category"] in FIXED_CATEGORIES), 2)
    variable_total = round(sum(c["total"] for c in categories if c["category"] not in FIXED_CATEGORIES), 2)

    # ── One-time charges this month ─────────────────────────────────────────────
    one_time_rows = conn.execute("""
        SELECT description, category,
               COALESCE(SUM(amount), 0) AS total,
               COUNT(*) AS count
        FROM transactions
        WHERE date >= ? AND date < ?
          AND type = 'debit' AND is_one_time = 1
          AND category NOT IN ('transfer','fees','investment')
        GROUP BY description, category
        ORDER BY total DESC
    """, (m_start, m_end)).fetchall()

    one_time_charges = [
        {"description": r["description"], "category": r["category"],
         "total": round(r["total"], 2), "count": r["count"]}
        for r in one_time_rows
    ]

    # ── Runway ─────────────────────────────────────────────────────────────────
    balance_row = conn.execute("""
        SELECT closing_balance
        FROM account_balances
        WHERE account = 'chequing'
        ORDER BY statement_month DESC LIMIT 1
    """).fetchone()
    td_balance = balance_row["closing_balance"] if balance_row else None

    burn_rows = conn.execute("""
        SELECT strftime('%Y-%m', date) AS mo,
               SUM(amount) AS monthly
        FROM transactions
        WHERE strftime('%Y-%m', date) >= ?
          AND type = 'debit'
          AND category NOT IN ('transfer','fees','investment')
          AND (is_one_time = 0 OR is_one_time IS NULL)
        GROUP BY mo
    """, (BURN_RATE_START,)).fetchall()
    avg_burn = (
        round(sum(r["monthly"] for r in burn_rows) / len(burn_rows), 2)
        if burn_rows else None
    )
    runway_months = (
        round(td_balance / avg_burn, 1)
        if td_balance and avg_burn and avg_burn > 0 else None
    )

    # ── Subscription transactions for this month ──────────────────────────────
    sub_rows = conn.execute("""
        SELECT description,
               COALESCE(SUM(amount), 0) AS total,
               COUNT(*) AS count,
               subcategory
        FROM transactions
        WHERE date >= ? AND date < ?
          AND type = 'debit'
          AND category = 'subscriptions'
        GROUP BY description, subcategory
        ORDER BY total DESC
    """, (m_start, m_end)).fetchall()

    subscriptions = [
        {
            "description": r["description"],
            "total":       round(r["total"], 2),
            "count":       r["count"],
            "subcategory": r["subcategory"],
        }
        for r in sub_rows
    ]

    conn.close()

    from datetime import date as _date
    today = _date.today()
    is_current = target == today.strftime("%Y-%m")

    return {
        "month":           target,
        "label":           _month_label(target),
        "is_current_month": is_current,
        "txn_count":       txn_count,
        "spent":           spent,
        "income":          income,
        "refunds":         refunds,
        "net":             net,
        "prev": {
            "month":  prev_label,
            "spent":  round(prev_totals["spent"], 2),
            "income": round(prev_totals["income"], 2),
        },
        "fixed_total":    fixed_total,
        "variable_total": variable_total,
        "runway_months":  runway_months,
        "avg_burn":       avg_burn,
        "categories":     categories,
        "one_time_charges": one_time_charges,
        "subscriptions":  subscriptions,
        "available_months": available,
    }


def _month_label(ym: str) -> str:
    """'2026-03' → 'March 2026'"""
    y, m = ym.split("-")
    import calendar
    return f"{calendar.month_name[int(m)]} {y}"


# ── POST /api/insights ─────────────────────────────────────────────────────────

@app.post("/api/insights")
def post_insights(month: Optional[str] = None):
    """
    Generate AI insights for the given month (YYYY-MM) using the configured backend.

    Backend is controlled by REPORT_BACKEND in config_local.py:
      "ollama"  — local Ollama, model set by REPORT_MODEL (default)
      "claude"  — Anthropic Claude API, requires ANTHROPIC_API_KEY

    Prompt is loaded from data/prompts/insights_prompt.txt at call time — edit
    the file to tune output without restarting the server.

    Returns: { insights, month, backend, model, error? }
    """
    from src.reporter import generate_insights, ReportError

    context = build_context()
    label   = _month_label(month) if month else "current month"

    try:
        insights = generate_insights(context=context, month=label)
    except ReportError as exc:
        return {
            "insights": [],
            "month":    month,
            "backend":  REPORT_BACKEND,
            "model":    REPORT_MODEL,
            "error":    str(exc),
        }

    return {
        "insights": insights,
        "month":    month,
        "backend":  REPORT_BACKEND,
        "model":    REPORT_MODEL,
    }


# ── /api/corrections ───────────────────────────────────────────────────────────
# Note: corrections.json CRUD is managed here directly.
# The categorizer also has _load_corrections() but that one strips _meta keys and
# is used for applying rules. These helpers are for reading/writing the raw file.

def _read_corrections_file() -> dict:
    """Return the parsed contents of corrections.json, or {} if the file is missing."""
    try:
        return json.loads(CORRECTIONS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _write_corrections_file(data: dict) -> None:
    """Serialise data to corrections.json with 2-space indent."""
    CORRECTIONS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


@app.get("/api/corrections")
def get_corrections():
    """Return all rules in corrections.json (excluding _comment/_format/_how_to_add meta keys)."""
    raw = _read_corrections_file()
    return {k: v for k, v in raw.items() if not k.startswith("_")}


@app.post("/api/corrections")
def add_correction(rule: CorrectionRule):
    """Add or update a correction rule. Key is stored uppercase."""
    if rule.category not in CATEGORIES:
        raise HTTPException(400, f"Invalid category '{rule.category}'.")
    raw = _read_corrections_file()
    key = rule.key.strip().upper()
    raw[key] = {"category": rule.category, "subcategory": rule.subcategory}
    _write_corrections_file(raw)
    return {"key": key, "category": rule.category, "subcategory": rule.subcategory}


@app.delete("/api/corrections/{key}")
def delete_correction(key: str) -> dict:
    """Remove a correction rule by key."""
    raw = _read_corrections_file()
    upper = key.upper()
    if upper not in raw:
        raise HTTPException(404, f"Rule '{upper}' not found.")
    del raw[upper]
    _write_corrections_file(raw)
    return {"deleted": upper}

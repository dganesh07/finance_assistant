"""
api.py — FastAPI backend for the Finance Agent dashboard.

Run:
  uvicorn api:app --reload --port 8000

Endpoints:
  GET  /api/summary                  — spending summary + runway
  GET  /api/transactions             — all transactions (filterable)
  GET  /api/transactions/review      — unconfirmed transactions only
  PATCH /api/transactions/{id}       — update category / confirm a row
  POST /api/transactions/confirm-all — bulk confirm by IDs
  GET  /api/bills                    — bills from bills.json
  POST /api/apply-corrections        — apply corrections.json to all unknowns (fast, no LLM)
  POST /api/run-categorizer          — trigger Ollama categorizer in background
  GET  /api/job/{job_id}             — poll background job status
  GET  /api/categories               — full category list from config
  POST /api/parse-statements         — scan statements folder, save raw rows to DB
  GET  /api/statements               — list files in data/statements/
  GET  /api/corrections              — view all rules in corrections.json
  POST /api/corrections              — add/update a correction rule
  DELETE /api/corrections/{key}      — remove a correction rule
"""

import json
import sqlite3
import uuid
from datetime import date, timedelta
from typing import List, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import BILLS_FILE, CATEGORIES, CORRECTIONS_FILE, DB_PATH, STATEMENTS_DIR, SUBCATEGORIES
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
               category, subcategory, confirmed, source_file, notes
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
        f"category, subcategory, confirmed, source_file, notes "
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
    if body.subcategory is not None: fields.append("subcategory = ?"); params.append(body.subcategory)
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
    job_id = str(uuid.uuid4())[:8]
    background_tasks.add_task(_run_categorizer_job, job_id)
    return {"job_id": job_id, "status": "started"}


# ── POST /api/apply-corrections ────────────────────────────────────────────────

@app.post("/api/apply-corrections")
def apply_corrections_endpoint():
    """
    Apply corrections.json to every transaction that is still 'unknown'.

    Fast — no LLM involved. Returns immediately with counts.
    Use this after saving new rules in the Review UI so newly-added corrections
    are stamped across all existing unconfirmed transactions instantly.
    """
    from src.categorizer import _load_corrections, _load_bill_rules, _apply_corrections

    conn     = get_conn()
    rows     = conn.execute("""
        SELECT id, description, amount, type, category
        FROM transactions
        WHERE (category IS NULL OR category IN ('unknown', '')) AND confirmed = 0
    """).fetchall()

    if not rows:
        conn.close()
        return {"applied": 0, "remaining_unknown": 0}

    bill_rules   = _load_bill_rules()
    corrections  = _load_corrections()
    merged_rules = {**bill_rules, **corrections}  # corrections win on conflict
    txns         = [dict(r) for r in rows]
    stamped, applied = _apply_corrections(txns, merged_rules)

    for result in stamped:
        if result.get("category") not in (None, "unknown", ""):
            conn.execute(
                "UPDATE transactions SET category = ?, subcategory = ?, confirmed = ? WHERE id = ?",
                (result["category"], result.get("subcategory"), result.get("confirmed", 1), result["id"]),
            )

    conn.commit()
    remaining = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE (category IS NULL OR category IN ('unknown','')) AND confirmed = 0"
    ).fetchone()[0]
    conn.close()

    return {"applied": applied, "remaining_unknown": remaining}


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


# ── /api/corrections ───────────────────────────────────────────────────────────

def _load_corrections() -> dict:
    try:
        return json.loads(CORRECTIONS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _save_corrections(data: dict) -> None:
    CORRECTIONS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


@app.get("/api/corrections")
def get_corrections():
    """Return all rules in corrections.json (excluding _comment/_format/_how_to_add meta keys)."""
    raw = _load_corrections()
    return {k: v for k, v in raw.items() if not k.startswith("_")}


@app.post("/api/corrections")
def add_correction(rule: CorrectionRule):
    """Add or update a correction rule. Key is stored uppercase."""
    if rule.category not in CATEGORIES:
        raise HTTPException(400, f"Invalid category '{rule.category}'.")
    raw = _load_corrections()
    key = rule.key.strip().upper()
    raw[key] = {"category": rule.category, "subcategory": rule.subcategory}
    _save_corrections(raw)
    return {"key": key, "category": rule.category, "subcategory": rule.subcategory}


@app.delete("/api/corrections/{key}")
def delete_correction(key: str):
    """Remove a correction rule by key."""
    raw = _load_corrections()
    upper = key.upper()
    if upper not in raw:
        raise HTTPException(404, f"Rule '{upper}' not found.")
    del raw[upper]
    _save_corrections(raw)
    return {"deleted": upper}

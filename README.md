# Finance Agent

A local personal finance agent. Drop bank statements into a folder, categorize with local AI, and get a rich financial context delivered to a report agent or chat interface.

---

## Project Structure

```
finance-agent/
├── run.py                        ← quick CLI entry point (import + DB init)
├── api.py                        ← FastAPI backend (18 endpoints)
├── config.py                     ← paths, settings, categories, BURN_RATE_START
├── dev.sh                        ← starts backend (port 8000) + frontend (port 5173)
├── db/
│   ├── schema.sql                ← SQLite table definitions (7 tables)
│   └── init_db.py                ← creates finance.db from schema
├── src/
│   ├── parser.py                 ← PDF + CSV parsing
│   ├── categorizer.py            ← Ollama AI categorizer + corrections pipeline
│   ├── context_builder.py        ← assembles DB + files into LLM-ready context block
│   └── reporter.py               ← AI report generator (Stage 5 — in progress)
├── scripts/
│   ├── ingest.py                 ← interactive full pipeline: parse → AI → review → save
│   ├── add_correction.py         ← add/update category rules in corrections.json
│   ├── check_db.py               ← read-only sanity check (row counts, quality, dupes)
│   ├── review.py                 ← interactive review UI for unconfirmed transactions
│   ├── reset_and_reimport.py     ← wipe + re-parse all statements
│   ├── test_categorizer.py       ← test Ollama categorizer behavior
│   ├── inspect_raw.py            ← debug raw pdfplumber output
│   └── wipe_db.py                ← clear all transaction rows (schema intact)
├── data/
│   └── statements/               ← drop PDFs / CSVs here (git-ignored)
├── frontend/                     ← React + Vite UI
├── docs/
│   ├── finance-app-architecture.html    ← visual architecture reference
│   └── finance-agent-build-prompts.md   ← staged build guide (4 phases)
├── bills.local.json              ← recurring bills config (git-ignored)
├── bills.example.json            ← template — copy to bills.local.json
├── profile.txt                   ← your financial DNA (git-ignored)
├── profile.example.txt           ← template — copy to profile.txt and fill in
├── financial_snapshot.json       ← external accounts (EQ, GICs, TFSA) — git-ignored
├── financial_snapshot.example.json ← template
└── finance.db                    ← SQLite database (auto-created, git-ignored)
```

---

## Installation

**Requirements:** Python 3.11+, Node.js (for frontend)

```bash
# Python backend
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt

# Frontend
cd frontend && npm install

# Start everything
./dev.sh
```

Or just the backend:
```bash
python run.py   # initializes DB and runs quick import
uvicorn api:app --reload --port 8000
```

---

## Build Stages

| Stage | Goal                          | Status        |
|-------|-------------------------------|---------------|
| 1     | Scaffold + SQLite schema      | Done          |
| 2     | PDF + CSV parser              | Done          |
| 3     | AI categorization (Ollama)    | Done          |
| 4     | Context builder               | Done          |
| 5     | Report agent                  | In progress   |

---

## Stage 2 — PDF + CSV Parser

**`src/parser.py`** — full statement parsing pipeline:

| Feature | Detail |
|---|---|
| CSV parsing | Auto-sniffs delimiter; handles TD headerless format and generic headers |
| PDF parsing | `pdfplumber` table extraction with a TD-specific parser; falls back to raw text for CC statements and generic text for other banks |
| CC text parsing | Handles TD Visa raw-text layout (no tables); uses tighter character spacing to restore merchant name word breaks |
| Date normalisation | `python-dateutil` handles any format; no-year dates (e.g. `FEB02`) infer year automatically |
| Deduplication | Two-layer: file-level skip (already imported?) + row-level MD5 hash check |
| Duplicate transactions | Same merchant, same date, same amount on the same day both insert correctly via an occurrence counter in the hash |
| Personal info scrubbing | Strips names from e-transfer and cheque descriptions before DB storage |
| Description cleanup | Strips TD chequing `_V`/`_F` transaction-type suffixes from merchant names |
| Account detection | Infers account label (`td_chequing`, `td_visa`, `td_savings`, `loc`) from filename |
| Pre-categorization rules | Obvious transactions (transfers, bills, income) are categorized at import with `confirmed=1` to lock them |
| Balance reconciliation | After import, parsed DB totals are checked against the statement's own declared figures |

### Running the parser

```bash
# Normal run — parses any new files in data/statements/
python run.py

# Full interactive pipeline for a single file (parse → AI → review → save)
python scripts/ingest.py data/statements/your-file.pdf

# Debug: inspect raw pdfplumber output for a PDF
python scripts/inspect_raw.py data/statements/your-file.pdf

# Re-parse a single file after a parser fix
python src/parser.py --reimport data/statements/your-file.pdf

# Wipe and re-parse everything
python scripts/reset_and_reimport.py
```

### Dropping in a statement

1. Download your statement from online banking (PDF or CSV)
2. Drop it into `data/statements/`
3. Run `python run.py` or use the UI import button

---

## Stage 3 — AI Categorizer

**`src/categorizer.py`** — Ollama-based categorization pipeline.

**Corrections hierarchy (highest to lowest priority):**
1. `data/corrections.json` — user-defined keyword rules (instant, no LLM)
2. `bills.local.json` match_keyword — bill-matching rules
3. Ollama `mistral:7b` — LLM batch inference (batch size: 20)

```bash
# Add a correction rule
python scripts/add_correction.py

# Run categorizer on all unknowns via API
POST /api/run-categorizer

# Apply corrections only (no LLM, instant)
POST /api/apply-corrections

# Test categorizer behavior
python scripts/test_categorizer.py
```

---

## Stage 4 — Context Builder

**`src/context_builder.py`** — assembles all financial data into a single plain-text block for LLM injection.

### What it sends to the AI

The context block is assembled **on demand** (not on import, not on a schedule). It contains:

| Section | Source | Detail |
|---|---|---|
| User Profile | `profile.txt` | Personal context: income, goals, stress areas, AI behavior notes |
| Monthly Spending | `transactions` table | Debit transactions grouped by category + month, from `BURN_RATE_START` (2026-01) onward. Excludes: transfer, fees, investment, one-time charges |
| One-time charges | `transactions` table | `is_one_time=1` rows shown separately, excluded from burn rate |
| Month-over-month delta | `transactions` table | % change in total + top 6 category shifts |
| Burn rate & runway | `transactions` + `account_balances` | Avg monthly spend (baseline months only) ÷ TD Chequing closing balance |
| Fixed obligations | `bills.local.json` | All active bills: name, amount, due day, autopay status, total |
| External accounts | `financial_snapshot.json` | EQ Bank HISA, Oaken HISA, GICs, TFSA balance + contribution room, net worth subtotal |
| GIC maturities | `financial_snapshot.json` | Any GIC maturing within 12 months with days remaining |
| Top 8 transactions | `transactions` table | Largest debits (last 90 days), excluding transfer/investment |
| Needs review | `transactions` table | Up to 15 uncategorized or unknown transactions, sorted by amount |

**Key filters applied to spending data:**
- `type = 'debit'`
- `is_one_time = 0 OR is_one_time IS NULL`
- `category NOT IN ('transfer', 'fees', 'investment')`
- Months on or after `BURN_RATE_START = "2026-01"` (Oct–Dec 2025 excluded as setup period)

**Note on income:** Salary (~$10K–$11K/month CAD) deposits to EQ Bank, NOT TD. The context builder explicitly warns the AI not to compute net income from transaction data. TD is a spending pool only — runway = TD balance ÷ burn rate.

### Triggering the context builder

```bash
# Inspect context output directly (CLI)
python -m src.context_builder

# Via API
GET /api/context

# Programmatically (from reporter or chat agent)
from src.context_builder import build_context
context_str = build_context()
```

The context builder runs **only when called** — there is no automatic trigger on import or schedule. It is meant to be called immediately before an LLM report or chat session.

---

## Stage 5 — Report Agent (In Progress)

`src/reporter.py` — will call `build_context()` and inject the result into a Claude/Ollama prompt to generate a structured monthly financial report.

**Push vs Pull design decision (open):**
- **Pull model:** UI button or CLI command triggers `build_context()` → LLM → report saved to `reports` table. User requests the report on demand.
- **Push model:** After each statement import, automatically run `build_context()` + report generation in the background. Report is ready in the UI when user opens it.

---

## Database Schema

7 tables in `finance.db`:

| Table | Purpose |
|---|---|
| `transactions` | All bank transactions (date, description, amount, type, account, category, subcategory, confirmed, is_one_time, notes) |
| `account_balances` | Statement opening/closing balances per account per month |
| `spending_periods` | Calendar month metadata (is_baseline, is_complete) |
| `bills` | Recurring obligations reference |
| `vehicles` | Vehicle info (insurance, gas avg) |
| `reports` | AI-generated reports (stored output) |
| `todo_items` | Action items extracted from reports |

### Key transaction flags

| Flag | Meaning |
|---|---|
| `confirmed = 1` | Category locked — AI and human review won't overwrite |
| `confirmed = 0` | Open for AI or human correction |
| `is_one_time = 1` | Excluded from burn rate average (large irregular purchase) |
| `is_one_time = 0` | Included in recurring spend baseline |

---

## API Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/context` | Full context block (build_context output) |
| `GET /api/summary` | Spending summary: totals, runway, review count |
| `GET /api/transactions` | All transactions (filterable) |
| `GET /api/transactions/review` | Unconfirmed transactions only |
| `PATCH /api/transactions/{id}` | Update category/subcategory/confirmed/is_one_time/notes |
| `POST /api/transactions/confirm-all` | Batch confirm multiple IDs |
| `POST /api/run-categorizer` | Trigger Ollama categorizer (background job) |
| `GET /api/job/{job_id}` | Poll background job status |
| `GET /api/bills` | Bills from bills.local.json |
| `POST /api/apply-corrections` | Apply corrections.json instantly (no LLM) |
| `POST /api/parse-statements` | Scan statements folder, insert raw rows |
| `GET /api/corrections` | View correction rules |
| `POST /api/corrections` | Add/update a correction rule |
| `DELETE /api/corrections/{key}` | Remove a correction rule |

---

## Customizing Bills

Edit `bills.local.json`. Each entry supports:

| Field | Type | Description |
|---|---|---|
| `name` | string | Display name |
| `amount` | number | Monthly cost in dollars |
| `frequency` | string | `"monthly"` or `"annual"` |
| `autopay` | boolean | `true` if auto-charged |
| `due_day` | int | Day of month (or `null`) |
| `account` | string | Which card/account pays this |
| `category` | string | Category tag for reporting |
| `subcategory` | string | Subcategory tag |
| `match_keyword` | string | Keyword to auto-match in transaction descriptions |
| `notes` | string | Freeform notes |

---

## Your Profile

`profile.txt` is git-ignored. Copy the template and fill it in:

```bash
cp profile.example.txt profile.txt
```

The AI reads this on every run to personalize advice. The file includes a `# CONTEXT BUILDER INSTRUCTIONS` section that is stripped before the text reaches the LLM.

---

## Financial Snapshot

`financial_snapshot.json` is git-ignored. Copy the template:

```bash
cp financial_snapshot.example.json financial_snapshot.json
```

Update this manually when external account balances change (EQ Bank, GICs, TFSA). It is not connected to any live API.

---

## Sanity Checks

```bash
# Read-only DB health check
python scripts/check_db.py

# Review unconfirmed transactions interactively
python scripts/review.py
```

---

## Inspecting the Database

No server needed — `finance.db` is a single file. Open it with
[DB Browser for SQLite](https://sqlitebrowser.org/dl/) via **File → Open Database**.

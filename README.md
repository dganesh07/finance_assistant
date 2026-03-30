# Finance Assistant

A local personal finance tool for TD Bank account holders. Drop bank statements into a folder, run the pipeline, review AI-assigned categories, and get a structured financial context block ready for an LLM report or chat session.

No cloud sync, no third-party services beyond Google Sheets (optional). Everything runs on your machine.

---

## Project Structure

```
finance-assistant/
├── api.py                        ← FastAPI backend (26 endpoints, port 8000)
├── config.py                     ← all paths, categories, Google Sheets config, BURN_RATE_START
├── dev.sh                        ← starts backend + frontend together
├── run.py                        ← quick CLI entry point (DB init + parse new statements)
├── db/
│   ├── schema.sql                ← SQLite table definitions (7 tables)
│   └── init_db.py                ← creates finance.db from schema + runs additive migrations
├── src/
│   ├── parser.py                 ← TD Bank PDF + CSV parsing, dedup, balance reconciliation
│   ├── categorizer.py            ← corrections rules + Ollama AI categorizer pipeline
│   ├── context_builder.py        ← assembles DB + files into LLM-ready context block
│   ├── sheets_connector.py       ← reads Accounts tab for expense context builder
│   ├── portfolio_connector.py    ← reads Accounts + Investment_Transactions tabs for portfolio view
│   └── reporter.py               ← AI insights agent (Ollama or Claude backend)
├── frontend/                     ← React + Vite UI (Dashboard, Monthly, Review, Transactions, Portfolio)
├── scripts/
│   ├── ingest.py                 ← interactive full pipeline: parse → AI → review → save
│   ├── check_db.py               ← read-only DB sanity check (row counts, quality, dupes)
│   ├── add_correction.py         ← add/update merchant → category rules
│   ├── review.py                 ← terminal-based transaction review
│   ├── reset_and_reimport.py     ← wipe + re-parse all statements
│   ├── inspect_raw.py            ← debug raw pdfplumber output for a PDF
│   ├── seed_db.py                ← snapshot current DB as finance.db.seed
│   ├── restore_seed.py           ← restore DB from finance.db.seed (after a dev wipe)
│   ├── wipe_db.py                ← clear all transaction rows (schema intact)
│   └── test_sheets.py            ← test Google Sheets connection
├── tests/
│   ├── test_api.py                 ← unit tests for FastAPI endpoints (TestClient, no LLM)
│   ├── test_context_builder.py     ← unit tests for context builder helpers
│   ├── test_categorizer.py         ← unit tests for categorizer (mocked Ollama, no LLM needed)
│   ├── test_sheets_connector.py    ← unit tests for Sheets column-matching logic
│   └── test_portfolio_connector.py ← unit tests for portfolio connector (mocked gspread)
├── data/
│   └── statements/               ← drop PDFs / CSVs here (git-ignored)
├── docs/
│   └── architecture.md           ← architecture diagrams and data flow reference
├── bills.local.json              ← recurring bills config (git-ignored)
├── bills.example.json            ← template — copy to bills.local.json
├── profile.txt                   ← your financial context for the AI (git-ignored)
├── profile.example.txt           ← template — copy to profile.txt and fill in
├── google_credentials.json       ← Google service account key (git-ignored)
└── finance.db                    ← SQLite database (auto-created, git-ignored)
```

---

## Setup

**Requirements:** Python 3.11+, Node.js 18+, [Ollama](https://ollama.ai) (for AI categorization)

### 1. Python environment

```bash
# Using uv (recommended)
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt

# Or standard venv
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Frontend

```bash
cd frontend && npm install
```

### 3. Copy and fill in config files

```bash
cp bills.example.json bills.local.json
cp profile.example.txt profile.txt
```

Edit each file:
- `bills.local.json` — your recurring bills (rent, subscriptions, utilities, etc.)
- `profile.txt` — your financial context: income range, goals, stress areas, AI behavior notes

### 4. Google Sheets setup (optional but recommended)

Two flows use Google Sheets — both use the same credentials and sheet ID:

**Expense context** (`sheets_connector.py`) — reads the `Accounts` tab and injects EQ Bank HISA, TFSA, GIC balances into the AI context block.

**Portfolio dashboard** (`portfolio_connector.py`) — reads both the `Accounts` tab and the `Investment_Transactions` tab to power the Portfolio view.

Setup steps:
1. Create a Google Cloud service account and download the JSON credentials key.
2. Save the key file as `google_credentials.json` in the project root.
3. Your sheet should have:
   - **Accounts** tab: `Account Name`, `Institution`, `Currency`, `Asset Class`, `Sub-Type`, `Balance`, `Include in Net Worth? (Y/N)`, `Notes`. Optional: `Interest Rate (base)`, `Interest Rate (promo)`, `Promo End Date`.
   - **Investment_Transactions** tab: `Date`, `Account`, `Ticker`, `Type (Buy/Sell/Dividend/Deposit/Withdrawal)`, `Units`, `Price`, `Total (Units*Price)`, `Fees`, `Notes`.
4. Share the sheet with the service account email (view access only).
5. Copy `config_local.example.py` → `config_local.py` and set `GOOGLE_SHEET_ID` to your sheet's ID.

`config_local.py` is git-ignored. Never put your sheet ID or credentials in `config.py`.

### 5. Ollama

```bash
# Install Ollama from https://ollama.ai, then pull the model
ollama pull mistral:7b
```

Ollama is only needed for the AI categorizer. The rest of the pipeline works without it.

### 6. Initialize the database

```bash
python db/init_db.py
```

### 7. Start both servers

```bash
./dev.sh
```

- API: http://localhost:8000
- UI: http://localhost:5173
- API docs: http://localhost:8000/docs

---

## How to Use

### Import a statement

1. Download a statement from TD online banking (PDF or CSV).
2. Drop it into `data/statements/`.
3. In the UI, go to **Review** and click **Import** — or from the terminal:

```bash
python run.py
```

Already-imported files are skipped automatically.

### Review transactions

Open the UI at http://localhost:5173 and go to **Review**.

The workflow:
1. **Import** — parses new PDFs/CSVs in `data/statements/`, saves raw rows with `category=unknown`.
2. **Apply Rules** — applies `data/corrections.json` instantly, no LLM. Use this after adding new correction rules.
3. **Run AI** — sends remaining unknowns to the Ollama categorizer (runs in background, ~20 transactions/batch).
4. **Confirm All** — approves all AI-assigned categories at once, or confirm row by row.

Per-row actions:
- Change the category/subcategory dropdown
- Click "save as rule" to write the merchant → category to `corrections.json` permanently
- Click `+` to add a note (stored with the transaction; visible as a tooltip on the Transactions page)
- Click `1×` to mark as one-time — excluded from burn rate calculations
- Confirm to lock the category

### Browse transactions

Go to **Transactions** in the UI. Filters: text search, category, date range, month picker. Click a category badge to edit inline. Click the `note` badge or `+` to add/edit notes inline. Click `1×` to toggle the one-time flag.

### Monthly breakdown

Go to **Monthly** in the UI for side-by-side month summaries and a category comparison table.

### Add a correction rule

Permanently teach the categorizer about a merchant:

```bash
python scripts/add_correction.py
# or
python scripts/add_correction.py 'NETFLIX' subscriptions
```

Rules in `data/corrections.json` always take priority over the AI.

### Browse by month with completeness info

The API exposes which months are fully covered vs partial:

```bash
curl http://localhost:8000/api/spending-periods
# Returns each month with is_complete (0/1), statement_start, statement_end
# Used by the dashboard month picker to show "Jan 1–27 (partial)" labels

curl "http://localhost:8000/api/monthly-subcategories?month=2026-01"
# Returns subcategory breakdown for January:
# [{ "category": "transport", "subcategory": "gas", "total": 46.31, "count": 2 }, ...]
```

### Build the AI context block

Assembles all financial data into a plain-text block for LLM injection:

```bash
python -m src.context_builder
```

Or via the API:

```bash
curl http://localhost:8000/api/context
```

### Portfolio dashboard

Go to **Portfolio** (`◎`) in the sidebar to see:
- Net worth summary (CAD total, USD total, TFSA balance + contribution room, 401K in USD)
- Full account table grouped by type (TFSA / Retirement / HISA / Savings / Other) with currency badges
- Invested vs Cash donut chart
- TFSA holdings (VFV.TO units + cost basis, CAD)
- US Retirement (FVTKX aggregated across employer match + ROTH + employee deferral, USD)
- Full Investment_Transactions log (newest first)

Everything is **read-only** — edit data directly in your Google Sheet and click ↺ Refresh.

### Test Google Sheets connection

```bash
python scripts/test_sheets.py
```

### DB sanity check

```bash
python scripts/check_db.py
```

### Snapshot and restore your real data during development

When your real transaction data is in a good state and you want to wipe the DB for testing without losing your work:

```bash
# Save current DB as a seed snapshot
python scripts/seed_db.py

# After testing / wiping — restore your real data
python scripts/restore_seed.py
```

`finance.db.seed` is git-ignored. The seed is a plain SQLite copy — no special tooling needed to inspect it.

### Run tests

```bash
python -m pytest tests/ -v
```

Tests require no LLM, no network, and no real DB — they use in-memory SQLite and mocked backends.

---

## Configuration Reference (`config.py`)

| Setting | Description |
|---|---|
| `DB_PATH` | Path to `finance.db` SQLite database |
| `STATEMENTS_DIR` | Where to drop statement PDFs/CSVs (`data/statements/`) |
| `BILLS_FILE` | Path to `bills.local.json` |
| `CORRECTIONS_FILE` | Path to `data/corrections.json` (merchant → category rules) |
| `PROFILE_FILE` | Path to `profile.txt` |
| `GOOGLE_SHEET_ID` | ID of your Google Sheet (from URL). Set to `""` to disable Sheets integration |
| `GOOGLE_CREDS_FILE` | Path to Google service account credentials JSON |
| `GOOGLE_ACCOUNTS_TAB` | Name of the Accounts tab (default: `"Accounts"`) — used by expense context |
| `GOOGLE_INVESTMENT_TAB` | Name of the investment transactions tab (default: `"Investment_Transactions"`) — used by portfolio view |
| `BURN_RATE_START` | `YYYY-MM` — earliest month included in burn rate calculations. Months before this are visible but excluded from the average (e.g. set to skip an unusually high setup period) |
| `OLLAMA_MODEL` | Ollama model for AI categorization (default: `"mistral:7b"`) |
| `OLLAMA_BASE_URL` | Ollama server address (default: `"http://localhost:11434"`) |
| `CATEGORIES` | List of valid spending categories (used by categorizer and UI dropdowns) |
| `SUBCATEGORIES` | Dict mapping categories to their allowed subcategory options |

---

## Data Privacy

The following files are git-ignored and never committed:

| File | Contains |
|---|---|
| `finance.db` | All transaction data |
| `data/statements/` | Bank statement PDFs and CSVs |
| `bills.local.json` | Your actual bill amounts and accounts |
| `profile.txt` | Your financial context and personal details |
| `google_credentials.json` | Google service account private key |
| `data/corrections.json` | Merchant names from your transactions |

The example/template files (`*.example.*`) contain no personal data and are safe to commit.

The parser strips names from e-transfer and cheque descriptions before writing to the database.

Nothing in this project sends data to any external service except:
- **Ollama** — runs locally, no data leaves your machine
- **Google Sheets** — read-only access to a sheet you control, using a service account you create

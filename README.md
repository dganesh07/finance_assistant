# Finance Agent

A local personal finance agent. Drop bank statements into a folder, run one command,
get a categorized spending report powered by a local AI model.

---

## Project Structure

```
finance-agent/
├── run.py                  ← main entry point
├── config.py               ← paths, settings, category list
├── db/
│   ├── schema.sql          ← SQLite table definitions
│   └── init_db.py          ← creates finance.db from schema
├── data/
│   └── statements/         ← drop PDFs / CSVs here
├── src/
│   ├── parser.py           ← PDF + CSV parsing (Phase 2)
│   ├── categorizer.py      ← AI categorization via Ollama (Phase 3)
│   ├── context_builder.py  ← assembles DB data for AI (Phase 4)
│   └── reporter.py         ← terminal report printer (Phase 5)
├── bills.json              ← your recurring bills
├── profile.txt             ← your financial profile (read by AI)
├── finance.db              ← SQLite database (auto-created, git-ignored)
└── requirements.txt
```

---

## Installation

**Requirements:** Python 3.11+

```bash
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
python run.py   # initialises DB and runs the agent
```

---

## Phase 2 — PDF + CSV Parser

### What was built

**`src/parser.py`** — full statement parsing pipeline:

| Feature | Detail |
|---|---|
| CSV parsing | Auto-sniffs delimiter; handles TD headerless format (no column row) and generic headers |
| PDF parsing | Uses `pdfplumber` table extraction with a TD-specific parser; falls back to raw text for non-TD PDFs |
| Date normalisation | `python-dateutil` handles any format; no-year dates (e.g. `FEB02`) infer year automatically |
| Deduplication | Two-layer: file-level skip (already imported?) + row-level MD5 hash check |
| Personal info scrubbing | Strips names from e-transfer descriptions before DB storage |
| Account number sanitisation | Strips account numbers from filenames before storing in `source_file` |
| Account detection | Infers account label (`td_chequing`, `td_visa`, etc.) from filename |
| Pre-categorization rules | Obvious transactions (transfers, bills, income) get a locked category at import time — AI won't overwrite |

**Schema additions:**
- `transactions.account` — tracks which bank account each transaction came from (prevents credit card double-counting in reports)

### Running the parser

```bash
# Normal run — parses any new files in data/statements/
python run.py

# Self-test with 5 fake transactions (no real statement needed)
python src/parser.py --test

# Debug: inspect raw pdfplumber output for a PDF
python src/parser.py --inspect data/statements/your-file.pdf
```

### Dropping in a statement

1. Download your statement from online banking (PDF or CSV)
2. Drop it into `data/statements/`
3. Run `python run.py`

The parser auto-detects the file, imports it, and skips it on future runs.

### Reset the database

```bash
rm finance.db
python run.py   # reinitialises from scratch
```

Your statement files in `data/statements/` are untouched — they'll re-import cleanly.

### Pre-categorization rules

Transactions matching known patterns are categorised and locked (`confirmed=1`) at import, before the AI runs in Phase 3. Add your own merchants to `_PRECATEGORY_RULES` in `src/parser.py`:

```python
(re.compile(r"LOBLAWS|SUPERSTORE|METRO", re.IGNORECASE), "groceries"),
(re.compile(r"TIM HORTONS|STARBUCKS",    re.IGNORECASE), "food_dining"),
```

### Credit card double-counting

Each transaction stores its `account` (e.g. `td_chequing`, `td_visa`). The Visa payment row in chequing (`TDVISAPREAUTHPYMT`) is auto-categorized as `transfer` and excluded from spending totals in Phase 4 reports. Individual Visa transactions are the real spending data.

### Phase 2 Checklist

- [ ] `python src/parser.py --test` → 5 inserted, run again → 0 inserted
- [ ] Drop a real bank statement → `python run.py` → transactions appear in DB
- [ ] Open `finance.db` → transactions table populated with correct account/category

---

## Inspecting the Database

No server needed — `finance.db` is a single file. Open it with [DB Browser for SQLite](https://sqlitebrowser.org/dl/) (free) via **File → Open Database**.

---

## Customizing Bills

Edit `bills.json` directly. Each entry supports:

| Field       | Type    | Description                        |
|-------------|---------|-------------------------------------|
| `name`      | string  | Display name                        |
| `amount`    | number  | Monthly cost in dollars             |
| `frequency` | string  | `"monthly"` or `"annual"`          |
| `autopay`   | boolean | `true` if auto-charged              |
| `due_day`   | int     | Day of month (or `null`)            |
| `account`   | string  | Which card/account pays this        |
| `category`  | string  | Category tag for reporting          |
| `notes`     | string  | Freeform notes                      |

---

## Your Profile

`profile.txt` is git-ignored. Copy the template and fill it in — the AI reads it on every run to personalise advice:

```bash
cp profile.example.txt profile.txt
```

---

## Build Stages

| Stage | Goal                          | Status     |
|-------|-------------------------------|------------|
| 1     | Scaffold + SQLite schema      | ✅ Done    |
| 2     | PDF + CSV parser              | ✅ Done    |
| 3     | AI categorization (Ollama)    | Upcoming   |
| 4     | Context builder               | Upcoming   |
| 5     | Terminal report               | Upcoming   |

---


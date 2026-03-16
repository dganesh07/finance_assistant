# Finance Agent

A local personal finance agent. Drop bank statements into a folder, run one command,
get a categorized spending report powered by a local AI model.

---

## Project Structure

```
finance-agent/
├── run.py                        ← main entry point
├── config.py                     ← paths, settings, category list
├── db/
│   ├── schema.sql                ← SQLite table definitions
│   └── init_db.py                ← creates finance.db from schema
├── src/
│   ├── parser.py                 ← PDF + CSV parsing (Phase 2)
│   ├── categorizer.py            ← AI categorization (Phase 3)
│   ├── context_builder.py        ← assembles DB data for AI (Phase 4)
│   └── reporter.py               ← terminal report printer (Phase 5)
├── scripts/
│   ├── reset_and_reimport.py     ← wipe + re-parse all statements
│   └── check_db.py               ← read-only sanity check before committing
├── data/
│   └── statements/               ← drop PDFs / CSVs here (git-ignored)
├── docs/                         ← architecture notes
├── bills.json                    ← recurring bills config
├── profile.example.txt           ← template — copy to profile.txt and fill in
├── profile.txt                   ← your financial profile (git-ignored)
└── finance.db                    ← SQLite database (auto-created, git-ignored)
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
| CSV parsing | Auto-sniffs delimiter; handles TD headerless format and generic headers |
| PDF parsing | `pdfplumber` table extraction with a TD-specific parser; falls back to raw text for CC statements and generic text for other banks |
| CC text parsing | Handles TD Visa raw-text layout (no tables); uses tighter character spacing to restore merchant name word breaks |
| Date normalisation | `python-dateutil` handles any format; no-year dates (e.g. `FEB02`) infer year automatically |
| Deduplication | Two-layer: file-level skip (already imported?) + row-level MD5 hash check |
| Duplicate transactions | Same merchant, same date, same amount on the same day both insert correctly via an occurrence counter in the hash |
| Split-column merge fix | Handles pdfplumber merging a debit and a credit from the same day into one table cell — both transactions are recovered |
| Personal info scrubbing | Strips names from e-transfer and cheque descriptions before DB storage |
| Description cleanup | Strips TD chequing `_V`/`_F` transaction-type suffixes from merchant names |
| Account number sanitisation | Strips card/account numbers from filenames before storing in `source_file` |
| Account detection | Infers account label (`chequing`, `creditcard`, `savings`, `loc`) from filename |
| Pre-categorization rules | Obvious transactions (transfers, bills, income) get a locked category at import — AI won't overwrite |
| Drop warnings | Any row with a dollar amount that couldn't be parsed as a transaction prints a `⚠ drop:` warning inline and increments the **Dropped** counter in the summary table |
| Balance reconciliation | After import, parsed DB totals are checked against the statement's own declared figures — `✓` or `⚠` per file |

### Running the parser

```bash
# Normal run — parses any new files in data/statements/
python run.py

# Self-test with synthetic transactions (no real statement needed)
python src/parser.py --test

# Debug: inspect raw pdfplumber output for a PDF
python src/parser.py --inspect data/statements/your-file.pdf
```

### Dropping in a statement

1. Download your statement from online banking (PDF or CSV)
2. Drop it into `data/statements/`
3. Run `python run.py`

The parser auto-detects the file, imports new rows, and skips it on future runs.

### Reset and reimport

Use this when testing parser changes or after replacing a statement file:

```bash
python scripts/reset_and_reimport.py
```

Clears all transaction rows (schema intact), re-parses every file in `data/statements/`,
then prints a reconciliation table, transfer highlights, and per-account summary.

### Sanity check before committing

```bash
python scripts/check_db.py
```

Read-only. Shows row counts, date ranges, category coverage, duplicate hash check,
data quality check, and a spot-check of recent transactions.

### Pre-categorization rules

Transactions matching known patterns are categorised and locked (`confirmed=1`) at import,
before the AI runs in Phase 3. Add your own merchants to `_PRECATEGORY_RULES` in
`src/parser.py`:

```python
(re.compile(r"LOBLAWS|SUPERSTORE|METRO", re.IGNORECASE), "groceries"),
(re.compile(r"TIM HORTONS|STARBUCKS",    re.IGNORECASE), "food_dining"),
```

### Credit card double-counting

Each transaction stores its `account` (e.g. `chequing`, `creditcard`). The Visa payment
row in chequing is auto-categorized as `transfer` and excluded from spending totals. The
individual Visa transactions are the real spending data. Both sides are cross-matched and
shown in the transfer highlights table.

---

## Inspecting the Database

No server needed — `finance.db` is a single file. Open it with
[DB Browser for SQLite](https://sqlitebrowser.org/dl/) (free) via **File → Open Database**.

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

`profile.txt` is git-ignored. Copy the template and fill it in — the AI reads it on every
run to personalise advice:

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

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

## Phase 1 Setup — First-Time Installation

### Prerequisites

- Python 3.11 or higher
- [uv](https://docs.astral.sh/uv/) — fast Python package manager

Install `uv` if you don't have it:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

### Step 1 — Navigate into the project folder

```bash
cd finance-agent
```

---

### Step 2 — Create a virtual environment with uv

```bash
uv venv
```

This creates a `.venv/` folder inside the project. Your Python packages stay
isolated here and won't conflict with anything else on your machine.

---

### Step 3 — Activate the virtual environment

**macOS / Linux:**
```bash
source .venv/bin/activate
```

**Windows:**
```bash
.venv\Scripts\activate
```

Your terminal prompt will show `(.venv)` when active.

---

### Step 4 — Install dependencies

```bash
uv pip install -r requirements.txt
```

---

### Step 5 — Initialize the database

```bash
python db/init_db.py
```

Expected output:
```
DB ready: /path/to/finance-agent/finance.db
All tables created successfully.
```

This creates `finance.db` in the project root with all five tables.
The file is just a single file on disk — no server, no daemon needed.

---

### Step 6 — Run the agent

```bash
python run.py
```

Expected output:
- A bills table showing your 5 configured bills and monthly total
- A DB tables table confirming all 5 SQLite tables exist

---

## Phase 1 Checklist (Tests to Pass)

- [ ] `python db/init_db.py` → runs without errors, `finance.db` appears
- [ ] `python run.py` → prints bills table + DB tables, no errors
- [ ] Open `finance.db` in **DB Browser for SQLite** → see 5 empty tables

---

## SQLite — Do You Need to Install Anything?

**No.** SQLite is built into Python's standard library (`import sqlite3`).
You do not need to install any database software.

`finance.db` is just a regular file. You can:
- Copy it anywhere to back it up
- Open it with a GUI to inspect data (see below)
- Delete it and re-run `db/init_db.py` to start fresh

### Recommended GUI: DB Browser for SQLite

Free, open-source, works on macOS/Windows/Linux.

Download: https://sqlitebrowser.org/dl/

After installing, open it and use **File → Open Database** to open `finance.db`.
You'll see all 5 tables under the "Database Structure" tab.

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

## Filling In Your Profile

`profile.txt` is **git-ignored** (personal data stays local). A blank template
is committed as `profile.example.txt`.

First-time setup:
```bash
cp profile.example.txt profile.txt
```

Then open `profile.txt` and fill in each section. The AI reads this file on
every run to personalize categorization and advice. The more honest detail you
add, the better the output.

---

## Build Stages

| Stage | Goal                          | Status     |
|-------|-------------------------------|------------|
| 1     | Scaffold + SQLite schema      | ✅ Done    |
| 2     | PDF + CSV parser              | Upcoming   |
| 3     | AI categorization (Ollama)    | Upcoming   |
| 4     | Context builder               | Upcoming   |
| 5     | Terminal report               | Upcoming   |

---

## Deactivating the Virtual Environment

When you're done working:
```bash
deactivate
```

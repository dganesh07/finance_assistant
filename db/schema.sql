-- Finance Agent — SQLite Schema
-- Applied by db/init_db.py on first run.
-- All CREATE TABLE statements use IF NOT EXISTS so re-running is safe.

CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT    NOT NULL,
    description TEXT    NOT NULL,
    amount      REAL    NOT NULL,
    type        TEXT    NOT NULL,         -- 'debit' or 'credit'
    account     TEXT    DEFAULT 'unknown', -- e.g. 'td_chequing', 'td_visa', 'td_savings'
    category    TEXT    DEFAULT 'unknown',
    subcategory TEXT,
    confirmed   INTEGER DEFAULT 0,        -- 1 = manually confirmed category
    is_one_time INTEGER DEFAULT 0,        -- 1 = one-off cost, excluded from burn rate average
    source_file TEXT,
    hash        TEXT    UNIQUE,           -- md5(date+description+amount), for dedup
    notes       TEXT,
    created_at  TEXT    DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bills (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,         -- "Rogers Wifi", "Netflix"
    amount      REAL    NOT NULL,
    frequency   TEXT    NOT NULL,         -- 'monthly' or 'annual'
    autopay     INTEGER DEFAULT 0,        -- 1 = yes
    due_day     INTEGER,                  -- day of month (e.g. 15 = 15th)
    account     TEXT,                     -- which card/account pays this
    category    TEXT,                     -- 'utilities', 'subscriptions', etc.
    active      INTEGER DEFAULT 1,
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS vehicles (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT,         -- "2019 Honda Civic"
    insurance_monthly       REAL,
    insurance_renewal_date  TEXT,
    gas_avg_monthly         REAL,
    last_service_date       TEXT,
    next_service_notes      TEXT,
    notes                   TEXT
);

CREATE TABLE IF NOT EXISTS reports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date     TEXT    DEFAULT CURRENT_TIMESTAMP,
    period_start TEXT,
    period_end   TEXT,
    summary_text TEXT,                    -- AI-generated narrative
    created_at   TEXT    DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS todo_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id   INTEGER REFERENCES reports(id),
    title       TEXT    NOT NULL,
    description TEXT,
    priority    TEXT,                     -- 'high', 'medium', 'low'
    type        TEXT,                     -- 'bill', 'review', 'action', 'heads-up'
    done        INTEGER DEFAULT 0,
    created_at  TEXT    DEFAULT CURRENT_TIMESTAMP
);

-- account_balances: opening/closing balance captured from each statement PDF.
-- The parser writes one row per account per statement month.
-- Used by context_builder to compute spending runway (TD balance ÷ monthly burn).
CREATE TABLE IF NOT EXISTS account_balances (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account         TEXT    NOT NULL,          -- 'chequing', 'creditcard', 'savings', 'loc'
    statement_month TEXT    NOT NULL,          -- 'YYYY-MM' — the statement period end month
    opening_balance REAL,                      -- balance at the start of the statement period
    closing_balance REAL,                      -- balance at the end of the statement period
    statement_start TEXT,                      -- official start date of the statement period (YYYY-MM-DD)
    statement_end   TEXT,                      -- official end date of the statement period (YYYY-MM-DD)
    source_file     TEXT,                      -- which statement filename this was read from
    captured_at     TEXT    DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(account, statement_month)           -- one balance row per account per month; re-parse updates it
);

-- spending_periods: one row per calendar month.
-- is_baseline = 0 → exclude from burn rate / average calculations (e.g. setup period).
-- is_complete = 1 → a full statement has been imported for this month.
-- Pre-populated by init_db for known setup months; auto-extended by parser as new statements arrive.
CREATE TABLE IF NOT EXISTS spending_periods (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    period_label TEXT    NOT NULL UNIQUE,      -- 'YYYY-MM', e.g. '2026-01'
    year         INTEGER NOT NULL,
    month        INTEGER NOT NULL,             -- 1–12
    is_baseline  INTEGER DEFAULT 1,            -- 0 = non-representative month, skip in baselines
    is_complete  INTEGER DEFAULT 0,            -- 1 = full statement imported
    notes        TEXT,
    UNIQUE(year, month)
);

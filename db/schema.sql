-- Finance Agent — SQLite Schema
-- Applied by db/init_db.py on first run.
-- All CREATE TABLE statements use IF NOT EXISTS so re-running is safe.

CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT    NOT NULL,
    description TEXT    NOT NULL,
    amount      REAL    NOT NULL,
    type        TEXT    NOT NULL,         -- 'debit' or 'credit'
    category    TEXT    DEFAULT 'unknown',
    subcategory TEXT,
    confirmed   INTEGER DEFAULT 0,        -- 1 = manually confirmed category
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

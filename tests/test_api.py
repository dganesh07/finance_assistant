"""
tests/test_api.py — Unit tests for the FastAPI backend endpoints.

Run:
  python -m pytest tests/test_api.py -v

All tests use:
  - FastAPI TestClient (no real server started)
  - Temporary in-memory SQLite DB (no finance.db required)
  - No Ollama, no Google Sheets, no network

The test DB is initialised from the real schema.sql so migrations and
IF NOT EXISTS guards are exercised identically to production.
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Allow importing from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient
from db.init_db import MIGRATIONS


# ── Temp-DB fixture ────────────────────────────────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    """
    Create a TestClient wired to an isolated temp SQLite DB.

    Builds the schema directly from schema.sql (bypassing init_db so that
    the module-level DB_PATH import in init_db doesn't interfere), then
    monkeypatches api.get_conn to use the temp DB.  Each test starts with
    a clean, fully-initialised schema.
    """
    import sqlite3
    import api as api_module
    from config import SCHEMA_FILE

    db_path = tmp_path / "test_finance.db"

    # Bootstrap temp DB from the real schema.sql + additive migrations
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_FILE.read_text())
    for _, sql in MIGRATIONS:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()
    conn.close()

    def _get_conn():
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(api_module, "get_conn", _get_conn)

    # Ensure no real bills/corrections files are read during tests
    monkeypatch.setattr(api_module, "BILLS_FILE",       tmp_path / "bills.json",       raising=False)
    monkeypatch.setattr(api_module, "CORRECTIONS_FILE", tmp_path / "corrections.json", raising=False)

    yield TestClient(api_module.app)


def _insert_txn(client, **kwargs):
    """
    Helper: POST /api/transactions with sensible defaults.
    Returns the created transaction dict.
    """
    defaults = {
        "date": "2026-01-15",
        "description": "TEST MERCHANT",
        "amount": 25.00,
        "type": "debit",
        "account": "td_chequing",
        "category": "groceries",
        "confirmed": 1,
    }
    defaults.update(kwargs)
    resp = client.post("/api/transactions", json=defaults)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── /api/categories ────────────────────────────────────────────────────────────

class TestCategories:
    def test_returns_list(self, client):
        resp = client.get("/api/categories")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_contains_expected_categories(self, client):
        data = client.get("/api/categories").json()
        for expected in ("groceries", "transport", "shopping", "income", "transfer"):
            assert expected in data, f"Missing category: {expected}"


# ── /api/subcategories ─────────────────────────────────────────────────────────

class TestSubcategories:
    def test_returns_dict(self, client):
        resp = client.get("/api/subcategories")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_categories_map_to_lists(self, client):
        data = client.get("/api/subcategories").json()
        for cat, subs in data.items():
            assert isinstance(subs, list), f"{cat} subcategories should be a list"


# ── /api/bills ─────────────────────────────────────────────────────────────────

class TestBills:
    def test_returns_empty_list_when_no_file(self, client):
        # BILLS_FILE is monkeypatched to a nonexistent path
        resp = client.get("/api/bills")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_bills_when_file_exists(self, client, tmp_path, monkeypatch):
        import api as api_module
        bills = [{"name": "Rent", "amount": 1650, "frequency": "monthly"}]
        bills_file = tmp_path / "bills_real.json"
        bills_file.write_text(json.dumps(bills))
        monkeypatch.setattr(api_module, "BILLS_FILE", bills_file, raising=False)

        resp = client.get("/api/bills")
        assert resp.status_code == 200
        assert resp.json() == bills


# ── /api/accounts ─────────────────────────────────────────────────────────────

class TestAccounts:
    def test_empty_db_returns_empty_list(self, client):
        resp = client.get("/api/accounts")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_distinct_accounts(self, client):
        _insert_txn(client, account="td_chequing")
        _insert_txn(client, account="td_visa",     description="VISA TXN")
        _insert_txn(client, account="td_chequing", description="ANOTHER CHEQUING")

        resp = client.get("/api/accounts")
        accounts = resp.json()
        assert sorted(accounts) == ["td_chequing", "td_visa"]


# ── /api/summary ───────────────────────────────────────────────────────────────

class TestSummary:
    def test_empty_db(self, client):
        resp = client.get("/api/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_out"] == 0.0
        assert data["total_in"] == 0.0
        assert data["net"] == 0.0
        assert data["runway_months"] is None
        assert data["review_count"] == 0
        assert data["by_category"] == []

    def test_counts_debits_correctly(self, client):
        _insert_txn(client, amount=50.00, category="groceries",  type="debit")
        _insert_txn(client, amount=30.00, category="transport",  type="debit")
        _insert_txn(client, amount=200.0, category="transfer",   type="debit")  # excluded

        resp  = client.get("/api/summary?days=365")
        data  = resp.json()
        # transfer is excluded from total_out
        assert data["total_out"] == pytest.approx(80.0, rel=0.01)

    def test_review_count_reflects_unconfirmed(self, client):
        _insert_txn(client, confirmed=0, description="UNREVIEWED 1")
        _insert_txn(client, confirmed=0, description="UNREVIEWED 2")
        _insert_txn(client, confirmed=1, description="CONFIRMED")

        data = client.get("/api/summary").json()
        assert data["review_count"] == 2

    def test_default_days_parameter(self, client):
        resp = client.get("/api/summary")
        assert "period" in resp.json()


# ── /api/transactions ──────────────────────────────────────────────────────────

class TestTransactions:
    def test_empty_db_returns_zero_total(self, client):
        resp = client.get("/api/transactions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["transactions"] == []

    def test_pagination_limit_offset(self, client):
        for i in range(5):
            _insert_txn(client, description=f"MERCHANT {i}", amount=10.0 * (i + 1))

        resp  = client.get("/api/transactions?limit=2&offset=0")
        data  = resp.json()
        assert data["total"] == 5
        assert len(data["transactions"]) == 2

        resp2 = client.get("/api/transactions?limit=2&offset=2")
        assert len(resp2.json()["transactions"]) == 2

    def test_filter_by_category(self, client):
        _insert_txn(client, category="groceries",  description="GROCERY STORE")
        _insert_txn(client, category="transport",  description="GAS STATION")

        resp = client.get("/api/transactions?category=groceries")
        txns = resp.json()["transactions"]
        assert all(t["category"] == "groceries" for t in txns)
        assert len(txns) == 1

    def test_filter_by_search(self, client):
        _insert_txn(client, description="AMAZON MARKETPLACE")
        _insert_txn(client, description="NETFLIX SUBSCRIPTION")

        txns = client.get("/api/transactions?search=amazon").json()["transactions"]
        assert len(txns) == 1
        assert "AMAZON" in txns[0]["description"]

    def test_filter_by_date_range(self, client):
        _insert_txn(client, date="2026-01-10", description="JAN TXN")
        _insert_txn(client, date="2026-02-10", description="FEB TXN")
        _insert_txn(client, date="2026-03-10", description="MAR TXN")

        txns = client.get("/api/transactions?date_from=2026-02-01&date_to=2026-02-28").json()["transactions"]
        assert len(txns) == 1
        assert txns[0]["description"] == "FEB TXN"


# ── /api/transactions/review ───────────────────────────────────────────────────

class TestReviewTransactions:
    def test_returns_only_unconfirmed(self, client):
        _insert_txn(client, confirmed=0, description="NEEDS REVIEW")
        _insert_txn(client, confirmed=1, description="ALREADY CONFIRMED")

        resp = client.get("/api/transactions/review")
        assert resp.status_code == 200
        txns = resp.json()
        assert len(txns) == 1
        assert txns[0]["description"] == "NEEDS REVIEW"
        assert txns[0]["confirmed"] == 0


# ── PATCH /api/transactions/{id} ───────────────────────────────────────────────

class TestUpdateTransaction:
    def test_update_category(self, client):
        txn = _insert_txn(client, category="groceries")
        resp = client.patch(f"/api/transactions/{txn['id']}", json={"category": "shopping"})
        assert resp.status_code == 200
        assert resp.json()["category"] == "shopping"

    def test_update_notes(self, client):
        txn = _insert_txn(client)
        resp = client.patch(f"/api/transactions/{txn['id']}", json={"notes": "birthday dinner"})
        assert resp.status_code == 200
        assert resp.json()["notes"] == "birthday dinner"

    def test_clear_notes_with_null(self, client):
        txn = _insert_txn(client, notes="old note")
        # notes sent explicitly as null should clear the value
        resp = client.patch(f"/api/transactions/{txn['id']}", json={"notes": None})
        assert resp.status_code == 200
        assert resp.json()["notes"] is None

    def test_update_subcategory(self, client):
        txn = _insert_txn(client, category="transport")
        resp = client.patch(f"/api/transactions/{txn['id']}",
                            json={"category": "transport", "subcategory": "gas"})
        assert resp.status_code == 200
        assert resp.json()["subcategory"] == "gas"

    def test_clear_subcategory_with_null(self, client):
        # Insert with a subcategory, then clear it
        txn = _insert_txn(client, category="transport")
        client.patch(f"/api/transactions/{txn['id']}", json={"subcategory": "gas"})
        resp = client.patch(f"/api/transactions/{txn['id']}", json={"subcategory": None})
        assert resp.status_code == 200
        assert resp.json()["subcategory"] is None

    def test_toggle_is_one_time(self, client):
        txn = _insert_txn(client)
        resp = client.patch(f"/api/transactions/{txn['id']}", json={"is_one_time": 1})
        assert resp.status_code == 200

    def test_invalid_category_returns_400(self, client):
        txn = _insert_txn(client)
        resp = client.patch(f"/api/transactions/{txn['id']}", json={"category": "not_a_real_category"})
        assert resp.status_code == 400

    def test_nonexistent_id_returns_404(self, client):
        resp = client.patch("/api/transactions/99999", json={"category": "groceries"})
        assert resp.status_code == 404

    def test_confirm_transaction(self, client):
        txn = _insert_txn(client, confirmed=0)
        resp = client.patch(f"/api/transactions/{txn['id']}", json={"confirmed": 1})
        assert resp.status_code == 200
        assert resp.json()["confirmed"] == 1


# ── POST /api/transactions (manual entry) ─────────────────────────────────────

class TestCreateTransaction:
    def test_creates_transaction(self, client):
        payload = {
            "date":        "2026-02-01",
            "description": "MANUAL ENTRY",
            "amount":      100.0,
            "type":        "debit",
            "account":     "td_chequing",
            "category":    "other",
            "confirmed":   1,
        }
        resp = client.post("/api/transactions", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["description"] == "MANUAL ENTRY"
        assert data["amount"]      == 100.0
        assert data["confirmed"]   == 1
        assert data["id"] is not None

    def test_duplicate_description_amount_date_gets_unique_hash(self, client):
        payload = {"date": "2026-02-01", "description": "DUPE", "amount": 10.0}
        resp1 = client.post("/api/transactions", json=payload)
        resp2 = client.post("/api/transactions", json=payload)
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp1.json()["id"] != resp2.json()["id"]

    def test_creates_with_notes_and_subcategory(self, client):
        resp = client.post("/api/transactions", json={
            "date": "2026-03-01", "description": "COFFEE", "amount": 5.50,
            "category": "food", "subcategory": "coffee", "notes": "espresso",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["notes"]      == "espresso"
        assert data["subcategory"] == "coffee"


# ── POST /api/transactions/confirm-all ────────────────────────────────────────

class TestConfirmAll:
    def test_confirms_multiple_transactions(self, client):
        t1 = _insert_txn(client, confirmed=0, description="UNCONF 1")
        t2 = _insert_txn(client, confirmed=0, description="UNCONF 2")
        t3 = _insert_txn(client, confirmed=0, description="UNCONF 3")

        resp = client.post("/api/transactions/confirm-all", json={"ids": [t1["id"], t2["id"]]})
        assert resp.status_code == 200

        # t1 and t2 confirmed; t3 still unconfirmed
        review = client.get("/api/transactions/review").json()
        review_ids = {t["id"] for t in review}
        assert t1["id"] not in review_ids
        assert t2["id"] not in review_ids
        assert t3["id"]     in review_ids

    def test_empty_ids_returns_zero(self, client):
        resp = client.post("/api/transactions/confirm-all", json={"ids": []})
        assert resp.status_code == 200
        assert resp.json()["updated"] == 0


# ── /api/monthly ───────────────────────────────────────────────────────────────

class TestMonthly:
    def test_empty_db_returns_empty_list(self, client):
        resp = client.get("/api/monthly")
        assert resp.status_code == 200
        assert resp.json()["months"] == []

    def test_groups_by_month(self, client):
        _insert_txn(client, date="2026-01-05", amount=100.0, category="groceries")
        _insert_txn(client, date="2026-01-20", amount=50.0,  category="transport")
        _insert_txn(client, date="2026-02-10", amount=75.0,  category="groceries")

        resp   = client.get("/api/monthly?months=12")
        months = resp.json()["months"]
        labels = [m["label"] for m in months]
        assert "2026-01" in labels
        assert "2026-02" in labels


# ── /api/corrections ───────────────────────────────────────────────────────────

class TestCorrections:
    def test_returns_empty_when_no_file(self, client):
        resp = client.get("/api/corrections")
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_add_and_retrieve_rule(self, client, tmp_path, monkeypatch):
        import api as api_module
        corr_file = tmp_path / "corrections_rw.json"
        corr_file.write_text("{}")
        monkeypatch.setattr(api_module, "CORRECTIONS_FILE", corr_file, raising=False)

        add_resp = client.post("/api/corrections", json={
            "key": "NETFLIX", "category": "subscriptions", "subcategory": "streaming"
        })
        assert add_resp.status_code == 200

        get_resp = client.get("/api/corrections")
        rules = get_resp.json()
        assert "NETFLIX" in rules
        assert rules["NETFLIX"]["category"] == "subscriptions"

    def test_delete_rule(self, client, tmp_path, monkeypatch):
        import api as api_module
        corr_file = tmp_path / "corrections_del.json"
        corr_file.write_text(json.dumps({"STARBUCKS": {"category": "food"}}))
        monkeypatch.setattr(api_module, "CORRECTIONS_FILE", corr_file, raising=False)

        del_resp = client.delete("/api/corrections/STARBUCKS")
        assert del_resp.status_code == 200
        assert client.get("/api/corrections").json() == {}


# ── /api/spending-periods ─────────────────────────────────────────────────────

class TestSpendingPeriods:
    def test_empty_db_returns_empty(self, client):
        resp = client.get("/api/spending-periods")
        assert resp.status_code == 200
        assert resp.json() == []


# ── /api/monthly-subcategories ────────────────────────────────────────────────

class TestMonthlySubcategories:
    def test_returns_empty_for_month_with_no_data(self, client):
        resp = client.get("/api/monthly-subcategories?month=2099-01")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_subcategory_breakdown(self, client):
        _insert_txn(client, date="2026-03-01", category="transport",
                    description="GAS 1", amount=40.0,
                    subcategory="gas")
        _insert_txn(client, date="2026-03-05", category="transport",
                    description="GAS 2", amount=45.0,
                    subcategory="gas")
        _insert_txn(client, date="2026-03-10", category="transport",
                    description="PARKING", amount=10.0,
                    subcategory="parking")

        resp = client.get("/api/monthly-subcategories?month=2026-03")
        assert resp.status_code == 200
        rows = resp.json()
        gas = next((r for r in rows if r["subcategory"] == "gas"), None)
        assert gas is not None
        assert gas["total"] == pytest.approx(85.0, rel=0.01)
        assert gas["count"] == 2


# ── /api/dashboard ────────────────────────────────────────────────────────────

class TestDashboard:
    def test_empty_db_returns_empty_available(self, client):
        resp = client.get("/api/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available_months"] == []
        assert data["month"] is None

    def test_response_shape_with_data(self, client):
        _insert_txn(client, date="2026-01-15", amount=100.0, category="groceries")
        _insert_txn(client, date="2026-01-20", amount=50.0,  category="transport", type="debit")
        _insert_txn(client, date="2026-01-25", amount=200.0, category="income",    type="credit")

        resp = client.get("/api/dashboard?month=2026-01")
        assert resp.status_code == 200
        data = resp.json()

        # Required top-level keys
        for key in ("month", "label", "is_current_month", "txn_count",
                    "spent", "income", "refunds", "net",
                    "prev", "fixed_total", "variable_total",
                    "runway_months", "avg_burn", "td_balance",
                    "accounts_covered", "categories",
                    "one_time_charges", "subscriptions", "available_months"):
            assert key in data, f"Missing key in dashboard response: {key}"

        assert data["month"] == "2026-01"
        assert isinstance(data["categories"], list)
        assert isinstance(data["available_months"], list)
        assert "2026-01" in data["available_months"]
        assert isinstance(data["prev"], dict)
        assert "spent" in data["prev"]
        assert "income" in data["prev"]

    def test_spent_excludes_transfer_and_fees(self, client):
        _insert_txn(client, date="2026-02-10", amount=100.0, category="groceries")
        _insert_txn(client, date="2026-02-11", amount=500.0, category="transfer")   # excluded
        _insert_txn(client, date="2026-02-12", amount=10.0,  category="fees")       # excluded

        resp = client.get("/api/dashboard?month=2026-02")
        data = resp.json()
        assert data["spent"] == pytest.approx(100.0, rel=0.01)

    def test_runway_null_when_no_td_balance(self, client):
        # No account_balances row → runway should be None
        _insert_txn(client, date="2026-02-10", amount=100.0, category="groceries")
        data = client.get("/api/dashboard?month=2026-02").json()
        assert data["runway_months"] is None


# ── /api/portfolio ────────────────────────────────────────────────────────────

class TestPortfolio:
    def test_returns_error_shape_when_not_configured(self, client, monkeypatch):
        import api as api_module
        monkeypatch.setattr(api_module, "GOOGLE_SHEET_ID", "", raising=False)

        resp = client.get("/api/portfolio")
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data
        assert data["accounts"] == []
        assert data["summary"] == {}
        assert data["investment_transactions"] == []
        assert data["holdings"] == {}

    def test_returns_error_shape_on_sheets_failure(self, client, monkeypatch):
        import api as api_module
        monkeypatch.setattr(api_module, "GOOGLE_SHEET_ID", "fake-sheet-id", raising=False)
        monkeypatch.setattr(api_module, "load_portfolio", lambda **_: None, raising=False)

        resp = client.get("/api/portfolio")
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data
        assert data["accounts"] == []


# ── /api/summary — runway fix verification ───────────────────────────────────

class TestSummaryRunwayFix:
    def test_runway_uses_td_balance_not_total_in(self, client):
        import sqlite3
        import api as api_module

        # Insert a transaction so avg_monthly > 0
        _insert_txn(client, date="2026-01-15", amount=300.0, category="groceries", type="debit")

        # Also insert a big credit that looks like a transfer — should NOT drive runway
        _insert_txn(client, date="2026-01-16", amount=5000.0, category="transfer", type="credit")

        # No account_balances row → runway must be None (not 5000 / burn)
        resp = client.get("/api/summary?days=365")
        data = resp.json()
        assert data["runway_months"] is None

    def test_runway_uses_td_chequing_balance(self, client, tmp_path, monkeypatch):
        import sqlite3
        import api as api_module

        # Wire a custom get_conn that includes account_balances
        db_path = tmp_path / "rw_test.db"
        from config import SCHEMA_FILE
        conn = sqlite3.connect(db_path)
        conn.executescript(SCHEMA_FILE.read_text())
        for _, sql in MIGRATIONS:
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass
        # Insert 30 days of spend at $100/day so avg_monthly ~$3000
        conn.execute(
            "INSERT INTO transactions (date, description, amount, type, category, confirmed, account) "
            "VALUES ('2026-01-15', 'GROCERIES', 3000.0, 'debit', 'groceries', 1, 'td_chequing')"
        )
        # TD chequing balance = $6000
        conn.execute(
            "INSERT INTO account_balances (account, statement_month, opening_balance, closing_balance) "
            "VALUES ('chequing', '2026-01', 7000.0, 6000.0)"
        )
        conn.commit()
        conn.close()

        def _get_conn():
            c = sqlite3.connect(db_path)
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(api_module, "get_conn", _get_conn)

        resp = api_module.app.build_middleware_stack  # just ensure import ok
        from fastapi.testclient import TestClient
        tc = TestClient(api_module.app)
        data = tc.get("/api/summary?days=365").json()
        # runway should be td_balance / avg_monthly = 6000 / ~3000 ≈ 2.0, NOT None
        assert data["runway_months"] is not None
        assert data["runway_months"] > 0

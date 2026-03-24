"""
src/context_builder.py — Assembles DB data + profile + external snapshot into AI-ready text.

This is pure data work — no LLM calls here. It reads from:
  1. SQLite (transactions, account_balances, spending_periods, bills table)
  2. profile.txt (user financial DNA — employment, goals, behavior notes)
  3. financial_snapshot.json (external accounts: EQ Bank, GICs, TFSA — manually updated)
  4. bills.local.json (recurring fixed obligations)

Call build_context() to get a formatted string ready to inject into any LLM prompt.
The same context feeds both the report agent and a future chat agent.

To see the output directly:
  python -m src.context_builder
  GET /api/context
"""

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from config import BILLS_FILE, BURN_RATE_START, DB_PATH, PROFILE_FILE

BASE_DIR = Path(__file__).parent.parent
SNAPSHOT_FILE = BASE_DIR / "financial_snapshot.json"

# Categories excluded from "spending" totals — money movements, not discretionary spend
_NON_SPEND = {"transfer", "fees", "investment", "income"}

# Spending categories excluded from burn rate (non-representative or internal)
_BURN_EXCLUDE = _NON_SPEND


# ── Helpers ────────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _fmt(amount: float) -> str:
    return f"${amount:,.2f}"


def _pct_change(old: float, new: float) -> str:
    if old == 0:
        return "n/a"
    delta = ((new - old) / old) * 100
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:.0f}%"


# ── Section builders ───────────────────────────────────────────────────────────

def _section_profile() -> str:
    try:
        text = PROFILE_FILE.read_text(encoding="utf-8").strip()
        # Trim the CONTEXT BUILDER INSTRUCTIONS block — that's for us, not the LLM
        if "CONTEXT BUILDER INSTRUCTIONS" in text:
            text = text[:text.index("CONTEXT BUILDER INSTRUCTIONS")].strip()
        return f"USER PROFILE\n{'─' * 60}\n{text}"
    except FileNotFoundError:
        return "USER PROFILE\n─\n[profile.txt not found — AI has no personal context]"


def _baseline_months(conn: sqlite3.Connection, limit: int = 3) -> list[str]:
    """
    Return the most recent N *likely-complete* calendar months on or after BURN_RATE_START.

    'Likely complete' means the month ended at least 5 weeks ago.
    Why: TD statements run mid-month to mid-month (e.g. Dec31–Jan27, Jan27–Feb28).
    If you import only one statement, the month it straddles will be partially covered.
    Waiting 5 weeks from month-end means the following statement has almost certainly
    been downloaded and imported, so both halves of that month are in the DB.

    This is a heuristic, not a guarantee — if you haven't imported a statement yet,
    that month's total will still be understated. The fix is: import all statements
    before reading burn rate numbers.
    """
    # 5 weeks ago — any month that ended before this is treated as likely complete
    cutoff = (date.today() - timedelta(weeks=5)).strftime("%Y-%m")

    rows = conn.execute("""
        SELECT DISTINCT strftime('%Y-%m', date) AS month
        FROM transactions
        WHERE strftime('%Y-%m', date) >= ?
          AND strftime('%Y-%m', date) <= ?
        ORDER BY month DESC
        LIMIT ?
    """, (BURN_RATE_START, cutoff, limit)).fetchall()
    return [r["month"] for r in rows]


def _section_monthly_spending(conn: sqlite3.Connection) -> str:
    """
    Monthly spending breakdown for baseline months only (on/after BURN_RATE_START).
    Shows last 3 complete baseline months + current month-to-date.
    """
    baseline_months = _baseline_months(conn, limit=3)

    if not baseline_months:
        return (
            f"MONTHLY SPENDING\n─\n"
            f"[No transactions found from {BURN_RATE_START} onwards yet]"
        )

    lines = [
        f"MONTHLY SPENDING (from {BURN_RATE_START} onwards — earlier months excluded from burn rate)",
             "─" * 60]

    month_data: dict[str, dict] = {}

    for month in sorted(baseline_months):
        month_start = f"{month}-01"
        # Last day: compute next month then subtract
        y, m = map(int, month.split("-"))
        if m == 12:
            next_m = f"{y+1}-01-01"
        else:
            next_m = f"{y}-{m+1:02d}-01"

        rows = conn.execute("""
            SELECT category,
                   COALESCE(SUM(amount), 0) AS total,
                   COUNT(*) AS cnt
            FROM transactions
            WHERE date >= ? AND date < ?
              AND type = 'debit'
              AND (is_one_time = 0 OR is_one_time IS NULL)
              AND category NOT IN ('transfer', 'fees', 'investment')
            GROUP BY category
            ORDER BY total DESC
        """, (month_start, next_m)).fetchall()

        one_time_rows = conn.execute("""
            SELECT description, category,
                   COALESCE(SUM(amount), 0) AS total
            FROM transactions
            WHERE date >= ? AND date < ?
              AND type = 'debit'
              AND is_one_time = 1
              AND category NOT IN ('transfer', 'fees', 'investment')
            GROUP BY description, category
            ORDER BY total DESC
        """, (month_start, next_m)).fetchall()

        total_spend   = sum(r["total"] for r in rows)
        one_time_total = sum(r["total"] for r in one_time_rows)
        month_data[month] = {
            "total": total_spend,
            "by_cat": {r["category"]: r["total"] for r in rows},
        }

        lines.append(f"\n  {month}  —  regular spend: {_fmt(total_spend)}")
        for r in rows:
            lines.append(f"    {r['category']:<18}  {_fmt(r['total']):>10}   ({r['cnt']} txns)")
        if one_time_rows:
            lines.append(f"    {'[one-time charges]':<18}  {_fmt(one_time_total):>10}   (excluded from burn rate)")
            for r in one_time_rows:
                lines.append(f"      ↳ {r['description'][:40]:<40}  {_fmt(r['total'])}  [{r['category']}]")

    # Month-over-month comparison (last two baseline months)
    if len(baseline_months) >= 2:
        sorted_months = sorted(baseline_months)
        prev, curr = sorted_months[-2], sorted_months[-1]
        prev_total = month_data[prev]["total"]
        curr_total = month_data[curr]["total"]
        change = _pct_change(prev_total, curr_total)
        lines.append(f"\n  Month-over-month ({prev} → {curr}): {_fmt(prev_total)} → {_fmt(curr_total)}  ({change})")

        # Per-category deltas
        prev_cats = month_data[prev]["by_cat"]
        curr_cats = month_data[curr]["by_cat"]
        all_cats = set(prev_cats) | set(curr_cats)
        deltas = []
        for cat in all_cats:
            p = prev_cats.get(cat, 0)
            c = curr_cats.get(cat, 0)
            if p > 0 or c > 0:
                deltas.append((cat, p, c, c - p))
        deltas.sort(key=lambda x: abs(x[3]), reverse=True)
        lines.append("  Category changes (largest first):")
        for cat, p, c, diff in deltas[:6]:
            sign = "+" if diff >= 0 else ""
            lines.append(f"    {cat:<18}  {_fmt(p):>10} → {_fmt(c):>10}   ({sign}{_fmt(diff)})")

    # Current month-to-date (if not a complete baseline month)
    today = date.today()
    current_label = today.strftime("%Y-%m")
    if current_label not in baseline_months:
        mtd_start = f"{current_label}-01"
        mtd_rows = conn.execute("""
            SELECT category, COALESCE(SUM(amount), 0) AS total, COUNT(*) AS cnt
            FROM transactions
            WHERE date >= ?
              AND type = 'debit'
              AND category NOT IN ('transfer', 'fees', 'investment')
            GROUP BY category
            ORDER BY total DESC
        """, (mtd_start,)).fetchall()
        if mtd_rows:
            mtd_total = sum(r["total"] for r in mtd_rows)
            lines.append(f"\n  {current_label} (month-to-date, {today.day} days in)  —  spend so far: {_fmt(mtd_total)}")
            for r in mtd_rows:
                lines.append(f"    {r['category']:<18}  {_fmt(r['total']):>10}   ({r['cnt']} txns)")

    return "\n".join(lines)


def _section_burn_and_runway(conn: sqlite3.Connection) -> str:
    """
    Average monthly burn from baseline months + TD runway from latest statement balance.
    """
    lines = ["BURN RATE & RUNWAY", "─" * 60]

    # Average monthly spend across all baseline months (on/after BURN_RATE_START)
    baseline_months = sorted(_baseline_months(conn, limit=12))  # up to 12 months back

    monthly_totals = []
    for month in baseline_months:
        y, m = map(int, month.split("-"))
        month_start = f"{month}-01"
        next_m = f"{y}-{m+1:02d}-01" if m < 12 else f"{y+1}-01-01"
        total = conn.execute("""
            SELECT COALESCE(SUM(amount), 0) AS t
            FROM transactions
            WHERE date >= ? AND date < ?
              AND type = 'debit'
              AND (is_one_time = 0 OR is_one_time IS NULL)
              AND category NOT IN ('transfer', 'fees', 'investment')
        """, (month_start, next_m)).fetchone()["t"]
        monthly_totals.append(total)

    if monthly_totals:
        avg_burn = sum(monthly_totals) / len(monthly_totals)
        lines.append(f"  Average monthly spend (months from {BURN_RATE_START} onwards: {', '.join(baseline_months)})")
        for month, total in zip(baseline_months, monthly_totals):
            lines.append(f"    {month}: {_fmt(total)}")
        lines.append(f"  Average monthly burn: {_fmt(avg_burn)}")
    else:
        avg_burn = 0
        lines.append("  [No baseline months data — cannot compute burn rate]")

    # TD balance from most recent account_balances row
    bal_row = conn.execute("""
        SELECT account, statement_month, closing_balance
        FROM account_balances
        WHERE account = 'chequing'
        ORDER BY statement_month DESC
        LIMIT 1
    """).fetchone()

    if bal_row:
        td_balance = bal_row["closing_balance"]
        lines.append(f"\n  TD Chequing closing balance: {_fmt(td_balance)}  (as of {bal_row['statement_month']} statement)")
        if avg_burn > 0:
            runway = td_balance / avg_burn
            lines.append(f"  TD Runway: {runway:.1f} months at current burn rate")
            lines.append(f"  NOTE: Income (EQ Bank) not included — actual runway is longer.")
            lines.append(f"  This is the spending pool only. Top-up from EQ as needed.")
    else:
        lines.append("\n  [No account_balances data — cannot compute runway]")

    return "\n".join(lines)


def _section_bills() -> str:
    lines = ["FIXED OBLIGATIONS (bills.json)", "─" * 60]
    try:
        bills = json.loads(BILLS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "\n".join(lines) + "\n  [bills.local.json not found]"

    active = [b for b in bills if b.get("active", True)]
    total = sum(b["amount"] for b in active)
    manual = [b["name"] for b in active if not b.get("autopay")]

    for b in sorted(active, key=lambda x: -x["amount"]):
        autopay_tag = "autopay" if b.get("autopay") else "MANUAL"
        due = f"due day {b['due_day']}" if b.get("due_day") else b.get("frequency", "")
        lines.append(f"  {b['name']:<28}  {_fmt(b['amount']):>8}   {due:<12}  [{autopay_tag}]")

    lines.append(f"\n  Total fixed monthly obligations: {_fmt(total)}")
    if manual:
        lines.append(f"  Manual payments needed this month: {', '.join(manual)}")

    return "\n".join(lines)


def _section_external_accounts() -> str:
    lines = ["EXTERNAL ACCOUNTS (financial_snapshot.json)", "─" * 60]

    if not SNAPSHOT_FILE.exists():
        lines.append("  [financial_snapshot.json not found]")
        lines.append("  Copy financial_snapshot.example.json → financial_snapshot.json and fill in.")
        lines.append("  Include: EQ Bank balance, GIC details, TFSA balance.")
        return "\n".join(lines)

    try:
        snap = json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return "\n".join(lines) + f"\n  [JSON parse error: {e}]"

    last_updated = snap.get("_last_updated", "unknown")
    lines.append(f"  Last updated: {last_updated}")

    # EQ Bank
    eq = snap.get("eq_bank", {})
    if eq.get("savings_balance"):
        rate = eq.get("hisa_rate_pct", 0)
        annual_interest = eq["savings_balance"] * rate / 100
        lines.append(f"\n  EQ Bank HISA")
        lines.append(f"    Balance:         {_fmt(eq['savings_balance'])}")
        lines.append(f"    Rate:            {rate}% — projected annual interest: {_fmt(annual_interest)}")
        if eq.get("notes"):
            lines.append(f"    Notes:           {eq['notes']}")

    # GICs
    gics = snap.get("gics", [])
    if gics:
        lines.append(f"\n  GICs")
        gic_total = 0
        for g in gics:
            principal = g.get("principal", 0)
            rate = g.get("rate_pct", 0)
            annual_int = principal * rate / 100
            gic_total += principal
            tfsa_tag = " [TFSA]" if g.get("is_tfsa") else ""
            lines.append(f"    {g.get('nickname','GIC')}{tfsa_tag}")
            lines.append(f"      Institution:   {g.get('institution','')}")
            lines.append(f"      Principal:     {_fmt(principal)}   @{rate}%   → {_fmt(annual_int)}/year interest")
            lines.append(f"      Maturity:      {g.get('maturity_date','unknown')}")
            if g.get("notes"):
                lines.append(f"      Notes:         {g['notes']}")
        lines.append(f"    Total in GICs:   {_fmt(gic_total)}")

    # TFSA
    tfsa = snap.get("tfsa", {})
    if tfsa.get("total_balance"):
        lines.append(f"\n  TFSA")
        lines.append(f"    Total balance:   {_fmt(tfsa['total_balance'])}")
        if tfsa.get("contribution_room_remaining"):
            lines.append(f"    Room remaining:  {_fmt(tfsa['contribution_room_remaining'])}")
        if tfsa.get("notes"):
            lines.append(f"    Notes:           {tfsa['notes']}")

    # Other accounts
    others = snap.get("other_accounts", [])
    for acct in others:
        if acct.get("balance"):
            lines.append(f"\n  {acct.get('nickname', acct.get('institution', 'Account'))}")
            lines.append(f"    Balance:         {_fmt(acct['balance'])}")

    # Upcoming income
    income_sources = snap.get("upcoming_income", [])
    if income_sources:
        lines.append(f"\n  Income context")
        for src in income_sources:
            amt = src.get("approximate_monthly_net_cad", 0)
            until = src.get("expected_until", "unknown")
            lines.append(f"    {src.get('source','')}")
            if amt:
                lines.append(f"      ~{_fmt(amt)}/month net   until {until}")
            if src.get("notes"):
                lines.append(f"      {src['notes']}")

    # Net worth summary
    td_note = "(check account_balances for TD balance)"
    eq_bal = eq.get("savings_balance", 0) if eq else 0
    gic_total_val = sum(g.get("principal", 0) for g in gics)
    tfsa_bal = tfsa.get("total_balance", 0) if tfsa else 0
    if eq_bal or gic_total_val or tfsa_bal:
        approx_net = eq_bal + gic_total_val + tfsa_bal
        lines.append(f"\n  Approximate net worth (excl. TD Chequing):")
        lines.append(f"    EQ HISA:         {_fmt(eq_bal)}")
        lines.append(f"    GICs:            {_fmt(gic_total_val)}")
        lines.append(f"    TFSA:            {_fmt(tfsa_bal)}")
        lines.append(f"    Subtotal:        {_fmt(approx_net)}  {td_note}")

    return "\n".join(lines)


def _section_upcoming_flags(conn: sqlite3.Connection) -> str:
    lines = ["UPCOMING FLAGS", "─" * 60]
    today = date.today()

    # GIC maturities from snapshot
    if SNAPSHOT_FILE.exists():
        try:
            snap = json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
            for g in snap.get("gics", []):
                mat = g.get("maturity_date")
                if mat and mat not in ("YYYY-MM-DD", "", None):
                    try:
                        mat_date = date.fromisoformat(mat)
                        days_to_mat = (mat_date - today).days
                        if 0 < days_to_mat < 365:
                            lines.append(f"  ℹ  GIC maturity:    {g.get('nickname','GIC')} ({g.get('institution','')}) — {mat}  ({days_to_mat} days)")
                            lines.append(f"     Principal {_fmt(g.get('principal',0))} + interest to redeploy.")
                    except ValueError:
                        pass
        except Exception:
            pass

    return "\n".join(lines)


def _section_unknowns(conn: sqlite3.Connection) -> str:
    rows = conn.execute("""
        SELECT id, date, description, amount, type
        FROM transactions
        WHERE (category IS NULL OR category IN ('unknown', ''))
          AND confirmed = 0
        ORDER BY amount DESC
        LIMIT 15
    """).fetchall()

    lines = ["TRANSACTIONS NEEDING REVIEW (unknown category, unconfirmed)", "─" * 60]
    if not rows:
        lines.append("  ✓ None — all transactions categorized")
        return "\n".join(lines)

    total_unknown = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE (category IS NULL OR category IN ('unknown','')) AND confirmed=0"
    ).fetchone()[0]

    lines.append(f"  {total_unknown} total unknown (showing top 15 by amount):")
    for r in rows:
        lines.append(f"  [{r['id']:4d}] {r['date']}  {_fmt(r['amount']):>10}  {r['description']}")

    return "\n".join(lines)


def _section_top_transactions(conn: sqlite3.Connection) -> str:
    rows = conn.execute("""
        SELECT date, description, amount, category, account
        FROM transactions
        WHERE type = 'debit'
          AND category NOT IN ('transfer', 'fees', 'investment')
          AND date >= ?
        ORDER BY amount DESC
        LIMIT 8
    """, ((date.today() - timedelta(days=90)).isoformat(),)).fetchall()

    lines = ["TOP TRANSACTIONS (last 90 days, excluding transfers/investment)", "─" * 60]
    for r in rows:
        lines.append(f"  {r['date']}  {_fmt(r['amount']):>10}  {r['category']:<16}  {r['description']}")

    return "\n".join(lines)


# ── Main entry point ───────────────────────────────────────────────────────────

def build_context() -> str:
    """
    Assembles the full financial context string for the AI agent.

    Returns a plain text block ready to inject into an LLM system or user prompt.
    Call this from the report agent or chat agent — not from the LLM itself.
    """
    conn = _conn()
    today = date.today()

    sections = [
        f"FINANCIAL CONTEXT SNAPSHOT",
        f"Generated: {today.isoformat()}",
        f"{'═' * 60}",
        "",
        _section_profile(),
        "",
        _section_monthly_spending(conn),
        "",
        _section_burn_and_runway(conn),
        "",
        _section_bills(),
        "",
        _section_external_accounts(),
        "",
        _section_upcoming_flags(conn),
        "",
        _section_top_transactions(conn),
        "",
        _section_unknowns(conn),
        "",
        f"{'═' * 60}",
        f"END OF CONTEXT",
    ]

    conn.close()
    return "\n".join(sections)


# ── CLI: run directly to inspect output ───────────────────────────────────────

if __name__ == "__main__":
    print(build_context())

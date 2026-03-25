"""
src/context_builder.py — Assembles DB data + profile into AI-ready spending context.

This is the SPENDING AGENT context — focused purely on TD Bank transactions.
It does NOT include external accounts (EQ Bank, TFSA, investments).
External accounts are handled by sheets_connector.py for a future portfolio agent.

Data sources used here:
  1. SQLite (transactions, account_balances)
  2. profile.txt — 3 key sections only: life timeline, income note, AI behaviour
  3. bills.local.json (recurring fixed obligations)

Disconnected (portfolio agent — not yet built):
  - financial_snapshot.json
  - Google Sheets / sheets_connector.py

Call build_context() to get a formatted string ready to inject into any LLM prompt.

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

# Categories excluded from spending totals — money movements, not discretionary spend
_NON_SPEND = {"transfer", "fees", "investment", "income"}

# SQL tuple form (used in NOT IN clauses).
# NOTE: Keep this in sync with _NON_SPEND if you add/remove categories.
_NON_SPEND_SQL = "('transfer', 'fees', 'investment', 'income')"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    """Open a read-only-style SQLite connection to DB_PATH with Row factory enabled."""
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


# ── PORTFOLIO AGENT (not yet built) ───────────────────────────────────────────
# _load_snapshot() is disconnected from the spending context.
# It will be re-wired when the portfolio agent is built.
# sheets_connector.py and financial_snapshot.json are still available.
#
# def _load_snapshot() -> dict:
#     """Load external accounts from Google Sheets or financial_snapshot.json."""
#     from config import GOOGLE_SHEET_ID, GOOGLE_CREDS_FILE, GOOGLE_ACCOUNTS_TAB
#     if GOOGLE_SHEET_ID and GOOGLE_CREDS_FILE.exists():
#         try:
#             from src.sheets_connector import load_from_sheets
#             data = load_from_sheets(GOOGLE_SHEET_ID, GOOGLE_CREDS_FILE, GOOGLE_ACCOUNTS_TAB)
#             if data:
#                 return data
#         except Exception as exc:
#             print(f"[context_builder] Sheets connector failed ({exc}), falling back to JSON")
#     snapshot_file = BASE_DIR / "financial_snapshot.json"
#     if not snapshot_file.exists():
#         return {}
#     try:
#         return json.loads(snapshot_file.read_text(encoding="utf-8"))
#     except json.JSONDecodeError:
#         return {}
# ──────────────────────────────────────────────────────────────────────────────


def _next_month(year: int, month: int) -> str:
    """Return YYYY-MM-DD string for the first day of the month after (year, month)."""
    if month == 12:
        return f"{year + 1}-01-01"
    return f"{year}-{month + 1:02d}-01"


# ── Section builders ───────────────────────────────────────────────────────────

def _section_key_context() -> str:
    """
    Extract the 3 essential framing blocks from profile.txt for the spending agent:
      1. LIFE TIMELINE   — baseline dates, contract end, setup period
      2. INCOME NOTE     — EQ Bank situation, why income isn't in TD data
      3. AI INSTRUCTIONS — tone, what not to flag, what to prioritise

    Much lighter than the full profile. Only what the AI needs to interpret spend data.
    Full profile.txt is preserved for the future chat/portfolio agent.
    """
    lines = ["KEY CONTEXT", "─" * 60]

    try:
        text = PROFILE_FILE.read_text(encoding="utf-8").strip()
        if "CONTEXT BUILDER INSTRUCTIONS" in text:
            text = text[:text.index("CONTEXT BUILDER INSTRUCTIONS")].strip()
    except FileNotFoundError:
        lines.append("  [profile.txt not found — AI has no framing context]")
        return "\n".join(lines)

    def _extract(start_marker: str, stop_markers: list[str], max_chars: int = 500) -> str:
        """Pull text from start_marker until the first stop_marker found."""
        if start_marker not in text:
            return ""
        start = text.index(start_marker)
        end   = len(text)
        for stop in stop_markers:
            pos = text.find(stop, start + len(start_marker))
            if 0 < pos < end:
                end = pos
        chunk = text[start:end].strip()
        if len(chunk) > max_chars:
            chunk = chunk[:max_chars].rsplit("\n", 1)[0]
        return chunk

    # 1. Life timeline — baseline, contract end, setup period
    timeline = _extract(
        "LIFE TIMELINE",
        ["INCOME\n", "FIXED MONTHLY", "SAVINGS & GOALS", "SPENDING PATTERNS"],
    )
    if timeline:
        lines.append("\n" + timeline)

    # 2. Income note — why income isn't visible in TD data
    income_note = _extract(
        "IMPORTANT — INCOME IS NOT VISIBLE",
        ["FIXED MONTHLY", "SAVINGS", "SPENDING PATTERNS", "VEHICLE", "FINANCIAL STRESS"],
    )
    if income_note:
        lines.append("\n" + income_note)

    # 3. AI behaviour — tone and priorities
    ai_notes = _extract(
        "AI BEHAVIOUR NOTES",
        ["═══", "CONTEXT BUILDER", "MONTHLY SPENDING"],
    )
    if ai_notes:
        lines.append("\n" + ai_notes)

    return "\n".join(lines)


def _section_profile() -> str:
    """Full profile — used by the future chat/portfolio agent, not the spending context."""
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
    Monthly spending breakdown for the last 3 complete baseline months + current MTD.
    Earlier months (before BURN_RATE_START) are excluded from all calculations here.
    """
    baseline_months = _baseline_months(conn, limit=3)

    if not baseline_months:
        return (
            f"MONTHLY SPENDING\n─\n"
            f"  [No complete months found from {BURN_RATE_START} onwards yet.\n"
            f"   Import statements and wait until a full month has passed to see burn rate data.]"
        )

    lines = [
        f"MONTHLY SPENDING (from {BURN_RATE_START} onwards — earlier months excluded from burn rate)",
        "─" * 60,
    ]

    month_data: dict[str, dict] = {}

    for month in sorted(baseline_months):
        y, m = map(int, month.split("-"))
        month_start = f"{month}-01"
        next_m = _next_month(y, m)

        rows = conn.execute(f"""
            SELECT category,
                   COALESCE(SUM(amount), 0) AS total,
                   COUNT(*) AS cnt
            FROM transactions
            WHERE date >= ? AND date < ?
              AND type = 'debit'
              AND (is_one_time = 0 OR is_one_time IS NULL)
              AND category NOT IN {_NON_SPEND_SQL}
            GROUP BY category
            ORDER BY total DESC
        """, (month_start, next_m)).fetchall()

        one_time_rows = conn.execute(f"""
            SELECT description, category,
                   COALESCE(SUM(amount), 0) AS total
            FROM transactions
            WHERE date >= ? AND date < ?
              AND type = 'debit'
              AND is_one_time = 1
              AND category NOT IN {_NON_SPEND_SQL}
            GROUP BY description, category
            ORDER BY total DESC
        """, (month_start, next_m)).fetchall()

        total_spend    = sum(r["total"] for r in rows)
        one_time_total = sum(r["total"] for r in one_time_rows)
        month_data[month] = {
            "total":  total_spend,
            "by_cat": {r["category"]: r["total"] for r in rows},
        }

        lines.append(f"\n  {month}  —  regular spend: {_fmt(total_spend)}")
        for r in rows:
            lines.append(f"    {r['category']:<18}  {_fmt(r['total']):>10}   ({r['cnt']} txns)")
        if one_time_rows:
            lines.append(f"    {'[one-time charges]':<18}  {_fmt(one_time_total):>10}   (excluded from burn rate)")
            for r in one_time_rows:
                lines.append(f"      ↳ {r['description'][:40]:<40}  {_fmt(r['total'])}  [{r['category']}]")

    # Month-over-month comparison (last two baseline months only)
    if len(baseline_months) >= 2:
        sorted_months = sorted(baseline_months)
        prev, curr = sorted_months[-2], sorted_months[-1]
        prev_total = month_data[prev]["total"]
        curr_total = month_data[curr]["total"]
        change = _pct_change(prev_total, curr_total)
        lines.append(f"\n  Month-over-month ({prev} → {curr}): {_fmt(prev_total)} → {_fmt(curr_total)}  ({change})")

        prev_cats = month_data[prev]["by_cat"]
        curr_cats = month_data[curr]["by_cat"]
        all_cats  = set(prev_cats) | set(curr_cats)
        deltas = [
            (cat, prev_cats.get(cat, 0), curr_cats.get(cat, 0), curr_cats.get(cat, 0) - prev_cats.get(cat, 0))
            for cat in all_cats
            if prev_cats.get(cat, 0) > 0 or curr_cats.get(cat, 0) > 0
        ]
        deltas.sort(key=lambda x: abs(x[3]), reverse=True)
        lines.append("  Category changes (largest first):")
        for cat, p, c, diff in deltas[:6]:
            sign = "+" if diff >= 0 else ""
            lines.append(f"    {cat:<18}  {_fmt(p):>10} → {_fmt(c):>10}   ({sign}{_fmt(diff)})")

    # Current month-to-date (if current month is not yet a complete baseline)
    today = date.today()
    current_label = today.strftime("%Y-%m")
    if current_label not in baseline_months:
        mtd_rows = conn.execute(f"""
            SELECT category, COALESCE(SUM(amount), 0) AS total, COUNT(*) AS cnt
            FROM transactions
            WHERE date >= ?
              AND type = 'debit'
              AND category NOT IN {_NON_SPEND_SQL}
            GROUP BY category
            ORDER BY total DESC
        """, (f"{current_label}-01",)).fetchall()
        if mtd_rows:
            mtd_total = sum(r["total"] for r in mtd_rows)
            lines.append(f"\n  {current_label} (month-to-date, {today.day} days in)  —  spend so far: {_fmt(mtd_total)}")
            for r in mtd_rows:
                lines.append(f"    {r['category']:<18}  {_fmt(r['total']):>10}   ({r['cnt']} txns)")

    return "\n".join(lines)


def _section_burn_and_runway(conn: sqlite3.Connection) -> str:
    """Average monthly burn from all baseline months + TD runway from latest statement balance."""
    lines = ["BURN RATE & RUNWAY", "─" * 60]

    baseline_months = sorted(_baseline_months(conn, limit=12))

    monthly_totals = []
    for month in baseline_months:
        y, m = map(int, month.split("-"))
        total = conn.execute(f"""
            SELECT COALESCE(SUM(amount), 0) AS t
            FROM transactions
            WHERE date >= ? AND date < ?
              AND type = 'debit'
              AND (is_one_time = 0 OR is_one_time IS NULL)
              AND category NOT IN {_NON_SPEND_SQL}
        """, (f"{month}-01", _next_month(y, m))).fetchone()["t"]
        monthly_totals.append(total)

    if monthly_totals:
        avg_burn = sum(monthly_totals) / len(monthly_totals)
        lines.append(f"  Average monthly spend (months from {BURN_RATE_START} onwards: {', '.join(baseline_months)})")
        for month, total in zip(baseline_months, monthly_totals):
            lines.append(f"    {month}: {_fmt(total)}")
        lines.append(f"  Average monthly burn: {_fmt(avg_burn)}")
    else:
        avg_burn = 0
        lines.append(f"  [No complete baseline months yet (need months ending before {(date.today() - timedelta(weeks=5)).strftime('%Y-%m')})]")

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
            lines.append(f"  [Runway not computable — no burn rate data yet]")
    else:
        lines.append("\n  [No account_balances data — import a chequing statement to compute runway]")

    return "\n".join(lines)


def _section_bills() -> str:
    lines = ["FIXED OBLIGATIONS (bills.json)", "─" * 60]
    try:
        bills = json.loads(BILLS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "\n".join(lines) + "\n  [bills.local.json not found — copy bills.example.json and fill in]"

    active = [b for b in bills if b.get("active", True)]
    if not active:
        return "\n".join(lines) + "\n  [No active bills found]"

    total  = sum(b["amount"] for b in active)
    manual = [b["name"] for b in active if not b.get("autopay")]

    for b in sorted(active, key=lambda x: -x["amount"]):
        autopay_tag = "autopay" if b.get("autopay") else "MANUAL"
        due = f"due day {b['due_day']}" if b.get("due_day") else b.get("frequency", "")
        lines.append(f"  {b['name']:<28}  {_fmt(b['amount']):>8}   {due:<12}  [{autopay_tag}]")

    lines.append(f"\n  Total fixed monthly obligations: {_fmt(total)}")
    if manual:
        lines.append(f"  Manual payments needed this month: {', '.join(manual)}")

    return "\n".join(lines)


# ── PORTFOLIO AGENT SECTIONS (not called from build_context — kept for later) ──

def _section_external_accounts(snap: dict) -> str:  # noqa: C901
    source = snap.get("_source", "json")
    source_label = "Google Sheets (live)" if source == "google_sheets" else "financial_snapshot.json"
    lines = [f"EXTERNAL ACCOUNTS ({source_label})", "─" * 60]

    if not snap:
        lines.append("  [No external account data available]")
        lines.append("  Either configure Google Sheets (GOOGLE_SHEET_ID in config.py)")
        lines.append("  or copy financial_snapshot.example.json → financial_snapshot.json and fill in.")
        return "\n".join(lines)

    warnings = []
    today = date.today()

    last_updated = snap.get("_last_updated", "unknown")
    lines.append(f"  Last updated: {last_updated}")
    if source != "google_sheets" and last_updated in ("unknown", "YYYY-MM-DD", ""):
        warnings.append("  ⚠  _last_updated is not set in financial_snapshot.json")

    # ── EQ Bank HISA ──────────────────────────────────────────────────────────
    eq = snap.get("eq_bank", {})
    eq_bal = eq.get("savings_balance", 0)
    if eq_bal and eq_bal > 0:
        base_rate  = eq.get("base_rate_pct") or eq.get("hisa_rate_pct", 0)
        promo_rate = eq.get("promo_rate_pct", 0)
        promo_end  = eq.get("promo_end_date", "")
        active_rate = promo_rate if promo_rate > 0 else base_rate
        annual_interest = eq_bal * active_rate / 100

        lines.append(f"\n  EQ Bank HISA")
        lines.append(f"    Balance:         {_fmt(eq_bal)}")

        if promo_rate > 0:
            lines.append(f"    Current rate:    {promo_rate}% (promo) — projected annual interest: {_fmt(annual_interest)}")
            lines.append(f"    Base rate:       {base_rate}% (if promo conditions not met)")
            if promo_end:
                lines.append(f"    Promo end:       {promo_end} — rate drops to {base_rate}% after income stops")
                # Flag if promo end is within 90 days
                try:
                    end_date = date.fromisoformat(promo_end)
                    days_left = (end_date - today).days
                    if 0 < days_left <= 90:
                        warnings.append(f"  ⚠  EQ Bank promo rate expires in {days_left} days ({promo_end}) — plan rate drop from {promo_rate}% → {base_rate}%")
                    elif days_left <= 0:
                        warnings.append(f"  ⚠  EQ Bank promo rate may have expired ({promo_end}) — verify current rate")
                except ValueError:
                    pass
        else:
            lines.append(f"    Rate:            {active_rate}% — projected annual interest: {_fmt(annual_interest)}")
    else:
        warnings.append("  ⚠  EQ Bank balance is 0 or missing")

    # ── GICs ──────────────────────────────────────────────────────────────────
    gics = snap.get("gics", [])
    real_gics = [g for g in gics if g.get("principal", 0) > 0]
    if real_gics:
        lines.append(f"\n  GICs")
        gic_total = 0
        for g in real_gics:
            principal  = g.get("principal", 0)
            rate       = g.get("rate_pct", 0)
            annual_int = principal * rate / 100
            gic_total += principal
            tfsa_tag   = " [TFSA]" if g.get("is_tfsa") else ""
            mat        = g.get("maturity_date", "unknown")
            if mat in ("YYYY-MM-DD", "", None):
                warnings.append(f"  ⚠  GIC '{g.get('nickname','unnamed')}' has no maturity date")
            lines.append(f"    {g.get('nickname','GIC')}{tfsa_tag}")
            lines.append(f"      Institution:   {g.get('institution','')}")
            lines.append(f"      Principal:     {_fmt(principal)}   @{rate}%   → {_fmt(annual_int)}/year interest")
            lines.append(f"      Maturity:      {mat}")
            if g.get("notes"):
                lines.append(f"      Notes:         {g['notes']}")
        lines.append(f"    Total in GICs:   {_fmt(gic_total)}")
    else:
        gic_total = 0

    # ── TFSA ──────────────────────────────────────────────────────────────────
    tfsa = snap.get("tfsa", {})
    tfsa_bal = tfsa.get("total_balance", 0)
    if tfsa_bal and tfsa_bal > 0:
        invested  = tfsa.get("invested_balance", 0)
        cash_held = tfsa.get("cash_balance", 0)
        room      = tfsa.get("contribution_room_remaining", 0)

        lines.append(f"\n  TFSA")
        lines.append(f"    Total balance:   {_fmt(tfsa_bal)}")

        # Show invested vs cash split if available
        if invested > 0 or cash_held > 0:
            lines.append(f"    Invested (ETF):  {_fmt(invested)}")
            lines.append(f"    Cash (uninvested): {_fmt(cash_held)}")
            if cash_held > 0:
                warnings.append(
                    f"  ℹ  TFSA has {_fmt(cash_held)} sitting in cash — consider investing "
                    f"(only relevant outside of spending planning)"
                )

        if room > 0:
            lines.append(f"    Room remaining:  {_fmt(room)}")

    # ── Other accounts — split CAD vs USD ─────────────────────────────────────
    other_all  = [a for a in snap.get("other_accounts", []) if a.get("balance", 0) > 0]
    other_cad  = [a for a in other_all if a.get("currency", "CAD") == "CAD"]
    other_usd  = [a for a in other_all if a.get("currency", "CAD") != "CAD"]

    if other_cad:
        lines.append(f"\n  Other CAD Accounts")
        for acct in other_cad:
            lines.append(f"    {acct.get('nickname', acct.get('institution', 'Account'))}"
                         f"  ({acct.get('institution', '')})")
            lines.append(f"      Balance: {_fmt(acct['balance'])}")

    if other_usd:
        usd_total = sum(a["balance"] for a in other_usd)
        lines.append(f"\n  USD / Foreign Accounts (excluded from CAD net worth)")
        for acct in other_usd:
            ccy = acct.get("currency", "USD")
            lines.append(f"    {acct.get('nickname', acct.get('institution', 'Account'))}"
                         f"  — {_fmt(acct['balance'])} {ccy}")

    # ── Upcoming income ───────────────────────────────────────────────────────
    income_sources = snap.get("upcoming_income", [])
    active_income = [s for s in income_sources if s.get("approximate_monthly_net_cad", 0) > 0]
    if active_income:
        lines.append(f"\n  Income context")
        for src in active_income:
            amt   = src.get("approximate_monthly_net_cad", 0)
            until = src.get("expected_until", "unknown")
            lines.append(f"    {src.get('source','')}")
            lines.append(f"      ~{_fmt(amt)}/month net   until {until}")
            if src.get("notes"):
                lines.append(f"      {src['notes']}")

    # ── Net worth summary (CAD only) ──────────────────────────────────────────
    other_total = sum(a.get("balance", 0) for a in other_cad)
    if eq_bal or gic_total or tfsa_bal or other_total:
        approx_net = eq_bal + gic_total + tfsa_bal + other_total
        lines.append(f"\n  Approximate net worth (excl. TD Chequing, excl. USD accounts):")
        if eq_bal:
            lines.append(f"    EQ HISA:         {_fmt(eq_bal)}")
        if gic_total:
            lines.append(f"    GICs:            {_fmt(gic_total)}")
        if tfsa_bal:
            lines.append(f"    TFSA:            {_fmt(tfsa_bal)}")
        if other_total:
            lines.append(f"    Other (CAD):     {_fmt(other_total)}")
        lines.append(f"    Subtotal:        {_fmt(approx_net)}  (add TD Chequing balance for full picture)")

    if warnings:
        lines.append(f"\n  NOTES & WARNINGS:")
        lines.extend(warnings)

    return "\n".join(lines)


def _section_upcoming_flags(snap: dict) -> str:  # portfolio agent
    lines = ["UPCOMING FLAGS", "─" * 60]
    today = date.today()
    found = False

    for g in snap.get("gics", []):
        mat = g.get("maturity_date")
        if mat and mat not in ("YYYY-MM-DD", "", None):
            try:
                mat_date   = date.fromisoformat(mat)
                days_to_mat = (mat_date - today).days
                if 0 < days_to_mat < 365:
                    lines.append(f"  ℹ  GIC maturity:    {g.get('nickname','GIC')} ({g.get('institution','')}) — {mat}  ({days_to_mat} days)")
                    lines.append(f"     Principal {_fmt(g.get('principal', 0))} + interest to redeploy.")
                    found = True
            except ValueError:
                pass

    if not found:
        lines.append("  (none in the next 12 months)")

    return "\n".join(lines)


def _section_top_transactions(conn: sqlite3.Connection) -> str:
    rows = conn.execute(f"""
        SELECT date, description, amount, category, account
        FROM transactions
        WHERE type = 'debit'
          AND category NOT IN {_NON_SPEND_SQL}
          AND date >= ?
        ORDER BY amount DESC
        LIMIT 8
    """, ((date.today() - timedelta(days=90)).isoformat(),)).fetchall()

    lines = ["TOP TRANSACTIONS (last 90 days, excluding transfers/investment)", "─" * 60]
    if not rows:
        lines.append("  (no transactions in the last 90 days)")
    for r in rows:
        lines.append(f"  {r['date']}  {_fmt(r['amount']):>10}  {r['category']:<16}  {r['description']}")

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


# ── Main entry point ───────────────────────────────────────────────────────────

def build_context() -> str:
    """
    Assembles the spending context string for the AI report agent.

    Covers: key framing, monthly spend breakdown, burn rate, fixed obligations,
    top transactions, and any uncategorised items needing review.

    External accounts (EQ Bank, TFSA, investments) are intentionally excluded —
    they belong to the portfolio agent (not yet built). See sheets_connector.py.

    Returns a plain text block ready to inject into any LLM prompt.
    """
    conn  = _conn()
    today = date.today()

    sections = [
        "FINANCIAL CONTEXT — SPENDING",
        f"Generated: {today.isoformat()}",
        "═" * 60,
        "",
        _section_key_context(),
        "",
        _section_monthly_spending(conn),
        "",
        _section_burn_and_runway(conn),
        "",
        _section_bills(),
        "",
        _section_top_transactions(conn),
        "",
        _section_unknowns(conn),
        "",
        "═" * 60,
        "END OF CONTEXT",
    ]

    conn.close()
    return "\n".join(sections)


# ── CLI: run directly to inspect output ───────────────────────────────────────

if __name__ == "__main__":
    print(build_context())

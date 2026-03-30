/**
 * Portfolio.jsx — Read-only view of all accounts and investment holdings.
 *
 * Data source: Google Sheets (via GET /api/portfolio)
 * No DB, no editing — the spreadsheet is the source of truth.
 *
 * Sections:
 *   1. Summary stat cards (CAD total, USD total, TFSA, 401K)
 *   2. Where your money is — account table + invested/cash donut
 *   3. Investment Holdings — TFSA holdings | US Retirement (401K)
 *   4. Transaction log — Investment_Transactions tab, newest first
 */

import { useState, useEffect, useCallback } from 'react'
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts'
import { api } from '../api.js'
import styles from './Portfolio.module.css'

// ── Formatting helpers ─────────────────────────────────────────────────────────

const fmtCAD = n =>
  `$${Math.abs(n).toLocaleString('en-CA', { minimumFractionDigits: 0 })}`

const fmtUSD = n =>
  `$${Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 0 })}`

const fmtAmt = (n, currency) => (currency === 'USD' ? fmtUSD(n) : fmtCAD(n))

const fmtUnits = n =>
  n % 1 === 0 ? n.toLocaleString() : n.toLocaleString(undefined, { maximumFractionDigits: 4 })

const fmtDate = iso => {
  if (!iso) return '—'
  const [y, m, d] = iso.split('-')
  const mon = new Date(Number(y), Number(m) - 1).toLocaleString('en-CA', { month: 'short' })
  return `${mon} ${Number(d)}, ${y}`
}

// ── Group + currency badge helpers ─────────────────────────────────────────────

const GROUP_LABEL = {
  tfsa:       'TFSA',
  retirement: 'Retirement',
  hisa:       'HISA',
  savings:    'Savings',
  other:      'Other',
}

const GROUP_STYLE = {
  tfsa:       styles.groupTfsa,
  retirement: styles.groupRetirement,
  hisa:       styles.groupHisa,
  savings:    styles.groupSavings,
  other:      styles.groupOther,
}

function CurrBadge({ currency }) {
  return (
    <span className={`${styles.currBadge} ${currency === 'USD' ? styles.currUSD : styles.currCAD}`}>
      {currency}
    </span>
  )
}

function GroupBadge({ group }) {
  return (
    <span className={`${styles.groupBadge} ${GROUP_STYLE[group] ?? styles.groupOther}`}>
      {GROUP_LABEL[group] ?? group}
    </span>
  )
}

// ── Transaction type colour ────────────────────────────────────────────────────

function TypeLabel({ type }) {
  const t = (type || '').toLowerCase()
  let cls = styles.typeDefault
  if (t === 'buy')                cls = styles.typeBuy
  else if (t === 'sell')          cls = styles.typeSell
  else if (t === 'dividend')      cls = styles.typeDiv
  else if (t === 'deposit' || t === 'contribution') cls = styles.typeDeposit
  return <span className={cls}>{type || '—'}</span>
}

// ── Donut tooltip ──────────────────────────────────────────────────────────────

function DonutTooltip({ active, payload }) {
  if (!active || !payload?.length) return null
  const { name, value, currency } = payload[0].payload
  return (
    <div className={styles.tooltip}>
      {name}: {fmtAmt(value, currency)}
    </div>
  )
}

// ── Main view ──────────────────────────────────────────────────────────────────

export default function Portfolio() {
  const [data,    setData]    = useState(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState(null)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    api.getPortfolio()
      .then(d => {
        if (d.error) setError(d.error)
        setData(d)
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  if (loading) return <p className={styles.loading}>loading portfolio…</p>

  const accounts  = data?.accounts  ?? []
  const summary   = data?.summary   ?? {}
  const txns      = data?.investment_transactions ?? []
  const holdings  = data?.holdings  ?? {}
  const updated   = data?._last_updated

  // ── Invested / Cash donut data ───────────────────────────────────────────────
  // Invested = TFSA + retirement (CAD/USD lumped separately)
  // Cash     = HISA + savings
  // Other    = everything else

  const investedCAD = accounts
    .filter(a => a.currency === 'CAD' && ['tfsa', 'retirement'].includes(a.group))
    .reduce((s, a) => s + a.balance, 0)

  const cashCAD = accounts
    .filter(a => a.currency === 'CAD' && ['hisa', 'savings'].includes(a.group))
    .reduce((s, a) => s + a.balance, 0)

  const otherCAD = accounts
    .filter(a => a.currency === 'CAD' && a.group === 'other')
    .reduce((s, a) => s + a.balance, 0)

  const retirementUSD = summary.retirement_usd ?? 0

  const donutData = [
    { name: 'Invested (CAD)', value: investedCAD,   currency: 'CAD', color: '#c084fc' },
    { name: 'Cash / HISA',    value: cashCAD,        currency: 'CAD', color: '#60a5fa' },
    { name: 'US Retirement',  value: retirementUSD,  currency: 'USD', color: '#f59e0b' },
    { name: 'Other',          value: otherCAD,        currency: 'CAD', color: '#475569' },
  ].filter(d => d.value > 0)

  // ── Render ───────────────────────────────────────────────────────────────────

  return (
    <div className={styles.page}>

      {/* Header */}
      <div className={styles.header}>
        <div>
          <div className={styles.title}>// portfolio</div>
          <div className={styles.subtitle}>
            {updated ? `last updated ${updated}` : 'google sheets — read only'}
          </div>
        </div>
        <button className={styles.refreshBtn} onClick={load} disabled={loading}>
          {loading ? 'loading…' : '↺ refresh'}
        </button>
      </div>

      {error && <p className={styles.error}>⚠ {error}</p>}

      {/* ── Stat cards ─────────────────────────────────────────────────────── */}
      <div className={styles.cards}>
        <div className={styles.card}>
          <div className={styles.cardLabel}>Total CAD</div>
          <div className={styles.cardValue}>{fmtCAD(summary.total_cad ?? 0)}</div>
          <div className={styles.cardSub}>all CAD accounts</div>
        </div>
        <div className={styles.card}>
          <div className={styles.cardLabel}>Total USD</div>
          <div className={styles.cardValue} style={{ color: 'var(--green)' }}>
            {fmtUSD(summary.total_usd ?? 0)}
          </div>
          <div className={styles.cardSub}>all USD accounts</div>
        </div>
        <div className={styles.card}>
          <div className={styles.cardLabel}>TFSA</div>
          <div className={styles.cardValue} style={{ color: 'var(--purple)' }}>
            {fmtCAD(summary.tfsa_balance ?? 0)}
          </div>
          <div className={styles.cardSub}>
            {summary.tfsa_contribution_room > 0
              ? `$${summary.tfsa_contribution_room.toLocaleString()} room left`
              : 'contribution room in notes'}
          </div>
        </div>
        <div className={styles.card}>
          <div className={styles.cardLabel}>US Retirement</div>
          <div className={styles.cardValue} style={{ color: 'var(--amber)' }}>
            {fmtUSD(summary.retirement_usd ?? 0)}
          </div>
          <div className={styles.cardSub}>401K — USD</div>
        </div>
      </div>

      {/* ── Where your money is ─────────────────────────────────────────────── */}
      <div className={styles.accountsRow}>

        {/* Account table */}
        <div className={styles.panel}>
          <div className={styles.panelTitle}>Where your money is</div>
          {accounts.length === 0
            ? <p className={styles.empty}>No accounts loaded.</p>
            : (
              <table className={styles.accountTable}>
                <thead>
                  <tr>
                    <th>Account</th>
                    <th>Type</th>
                    <th>Currency</th>
                    <th style={{ textAlign: 'right' }}>Balance</th>
                  </tr>
                </thead>
                <tbody>
                  {accounts.map((acct, i) => (
                    <tr key={i}>
                      <td>
                        <div className={styles.acctName}>{acct.name}</div>
                        {acct.institution && (
                          <div className={styles.acctInst}>{acct.institution}</div>
                        )}
                      </td>
                      <td><GroupBadge group={acct.group} /></td>
                      <td><CurrBadge currency={acct.currency} /></td>
                      <td className={styles.acctBal}>
                        {fmtAmt(acct.balance, acct.currency)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
          }
        </div>

        {/* Invested vs Cash donut */}
        <div className={styles.panel}>
          <div className={styles.panelTitle}>Allocation</div>
          <div className={styles.donutWrap}>
            {donutData.length > 0 && (
              <ResponsiveContainer width="100%" height={160}>
                <PieChart>
                  <Pie
                    data={donutData}
                    cx="50%"
                    cy="50%"
                    innerRadius={48}
                    outerRadius={72}
                    dataKey="value"
                    stroke="none"
                  >
                    {donutData.map((d, i) => (
                      <Cell key={i} fill={d.color} />
                    ))}
                  </Pie>
                  <Tooltip content={<DonutTooltip />} />
                </PieChart>
              </ResponsiveContainer>
            )}
            <div className={styles.donutLegend}>
              {donutData.map((d, i) => (
                <div key={i} className={styles.donutLegendRow}>
                  <span className={styles.donutDot} style={{ background: d.color }} />
                  <span className={styles.donutLegendLabel}>{d.name}</span>
                  <span className={styles.donutLegendAmt}>{fmtAmt(d.value, d.currency)}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* ── Investment Holdings ─────────────────────────────────────────────── */}
      <div className={styles.holdingsRow}>

        {/* TFSA */}
        <div className={styles.holdingCard}>
          <div className={styles.holdingTitle}>TFSA Holdings</div>
          <div className={styles.holdingSubtitle}>
            registered account · CAD · future: live price data
          </div>
          {(holdings['TFSA'] ?? []).length === 0
            ? <p className={styles.empty}>No TFSA holdings recorded.</p>
            : (holdings['TFSA'] ?? []).map((h, i) => (
              <div key={i} className={styles.holdingRow}>
                <span className={styles.holdingTicker}>{h.ticker}</span>
                <span className={styles.holdingUnits}>{fmtUnits(h.total_units)} units</span>
                <span className={styles.holdingCost}>{fmtAmt(h.cost_basis, h.currency)}</span>
                <span className={styles.holdingCurr}><CurrBadge currency={h.currency} /></span>
              </div>
            ))
          }
        </div>

        {/* US Retirement — 401K */}
        <div className={styles.holdingCard}>
          <div className={styles.holdingTitle}>US Retirement — 401K</div>
          <div className={styles.holdingSubtitle}>
            fidelity · USD · employer match + roth + employee deferral
          </div>
          {(holdings['401K'] ?? []).length === 0
            ? <p className={styles.empty}>No 401K holdings recorded.</p>
            : (holdings['401K'] ?? []).map((h, i) => (
              <div key={i} className={styles.holdingRow}>
                <span className={styles.holdingTicker}>{h.ticker}</span>
                <span className={styles.holdingUnits}>{fmtUnits(h.total_units)} units</span>
                <span className={styles.holdingCost}>{fmtAmt(h.cost_basis, h.currency)}</span>
                <span className={styles.holdingCurr}><CurrBadge currency={h.currency} /></span>
              </div>
            ))
          }
        </div>
      </div>

      {/* ── Investment Transactions log ──────────────────────────────────────── */}
      <div className={styles.panel}>
        <div className={styles.panelTitle}>Investment Transactions</div>
        {txns.length === 0
          ? <p className={styles.empty}>No transactions loaded.</p>
          : (
            <table className={styles.txnTable}>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Account</th>
                  <th>Ticker</th>
                  <th>Type</th>
                  <th style={{ textAlign: 'right' }}>Units</th>
                  <th style={{ textAlign: 'right' }}>Price</th>
                  <th style={{ textAlign: 'right' }}>Total</th>
                  <th>Currency</th>
                  <th>Notes</th>
                </tr>
              </thead>
              <tbody>
                {txns.map((t, i) => (
                  <tr key={i}>
                    <td className={styles.txnDate}>{fmtDate(t.date)}</td>
                    <td className={styles.txnAcct}>{t.account}</td>
                    <td className={styles.txnTicker}>{t.ticker || '—'}</td>
                    <td><TypeLabel type={t.type} /></td>
                    <td className={styles.txnUnits}>{t.units ? fmtUnits(t.units) : '—'}</td>
                    <td className={styles.txnPrice}>{t.price ? fmtAmt(t.price, t.currency) : '—'}</td>
                    <td className={styles.txnTotal}>{t.total ? fmtAmt(t.total, t.currency) : '—'}</td>
                    <td><CurrBadge currency={t.currency} /></td>
                    <td className={styles.txnNotes} title={t.notes}>{t.notes || ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        }
      </div>

    </div>
  )
}

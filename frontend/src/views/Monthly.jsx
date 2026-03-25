import { useState, useEffect } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, Cell,
} from 'recharts'
import { api } from '../api.js'
import styles from './Monthly.module.css'

/*
 * Monthly view — side-by-side monthly spending breakdown.
 *
 * Shows:
 *   - One summary card per month (total out / income / net)
 *   - Category comparison table: rows = categories, columns = months
 *   - Month-over-month delta on the two most recent months
 */

const fmt = n => `$${Math.abs(n).toLocaleString('en-CA', { minimumFractionDigits: 2 })}`

const MONTH_LABEL = label => {
  const [y, m] = label.split('-')
  return new Date(y, m - 1).toLocaleString('en-CA', { month: 'short', year: 'numeric' })
}

const pctChange = (prev, curr) => {
  if (!prev) return null
  const delta = ((curr - prev) / prev) * 100
  return delta
}

// ── Month summary card ──────────────────────────────────────────────────────────

// Short account label for badges: 'chequing' → 'CHQ', 'creditcard' → 'CC', etc.
const ACCT_SHORT = { chequing: 'CHQ', creditcard: 'CC', savings: 'SAV', loc: 'LOC' }
const acctShort = a => ACCT_SHORT[a] ?? a.toUpperCase().slice(0, 4)

function AccountBadges({ accounts }) {
  if (!accounts?.length) return null
  return (
    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 4 }}>
      {accounts.map(a => (
        <span
          key={a.account}
          title={`${a.statement_start} → ${a.statement_end}`}
          style={{
            fontSize: 10,
            fontFamily: 'var(--font-mono)',
            padding: '1px 5px',
            borderRadius: 3,
            background: a.covers_month ? '#1a3a1a' : '#2a2010',
            color:      a.covers_month ? '#4ade80'  : '#f59e0b',
            border:    `1px solid ${a.covers_month ? '#4ade8033' : '#f59e0b33'}`,
          }}
        >
          {acctShort(a.account)} {a.covers_month ? '✓' : '~'}
        </span>
      ))}
    </div>
  )
}

function MonthCard({ month, isLatest }) {
  const netPositive = month.net >= 0
  const hasOneTime  = month.one_time_out > 0
  return (
    <div className={`${styles.monthCard} ${isLatest ? styles.monthCardLatest : ''}`}>
      <div className={styles.monthCardTitle}>{MONTH_LABEL(month.label)}</div>
      <AccountBadges accounts={month.accounts_covered} />

      {/* Regular spend */}
      <div className={styles.monthCardRow}>
        <span className={styles.monthCardLabel}>regular</span>
        <span className={styles.monthCardOut}>{fmt(month.regular_out)}</span>
      </div>

      {/* One-time spend — highlighted differently */}
      {hasOneTime && (
        <div className={styles.monthCardRow}>
          <span className={styles.monthCardLabel} style={{ color: 'var(--amber)' }}>one-time</span>
          <span className={styles.monthCardOneTime}>{fmt(month.one_time_out)}</span>
        </div>
      )}

      {month.refunds > 0 && (
        <div className={styles.monthCardRow}>
          <span className={styles.monthCardLabel}>refunds</span>
          <span className={styles.monthCardRefund}>−{fmt(month.refunds)}</span>
        </div>
      )}
      <div className={styles.monthCardRow}>
        <span className={styles.monthCardLabel}>income</span>
        <span className={styles.monthCardIn}>{fmt(month.total_in)}</span>
      </div>
      <div className={styles.monthCardDivider} />
      <div className={styles.monthCardRow}>
        <span className={styles.monthCardLabel}>net</span>
        <span
          className={styles.monthCardNet}
          style={{ color: netPositive ? 'var(--green)' : 'var(--red)' }}
        >
          {netPositive ? '+' : '−'}{fmt(month.net)}
        </span>
      </div>
    </div>
  )
}

// ── Category comparison table ───────────────────────────────────────────────────

function CategoryTable({ months }) {
  // months is ordered newest-first from API — reverse to show oldest → newest left-to-right
  const ordered = [...months].reverse()

  // Collect all unique categories that appear in any month's spending
  const allCats = []
  const seen = new Set()
  for (const mo of ordered) {
    for (const row of mo.by_category) {
      if (!seen.has(row.category)) {
        seen.add(row.category)
        allCats.push(row.category)
      }
    }
  }

  // Build lookup: month.label → { category → { total, count } }
  const lookup = {}
  for (const mo of ordered) {
    lookup[mo.label] = {}
    for (const row of mo.by_category) {
      lookup[mo.label][row.category] = row
    }
  }

  // Sort categories by total in the most recent month (desc)
  const latestLabel = ordered[ordered.length - 1]?.label
  allCats.sort((a, b) => {
    const aTotal = lookup[latestLabel]?.[a]?.total ?? 0
    const bTotal = lookup[latestLabel]?.[b]?.total ?? 0
    return bTotal - aTotal
  })

  // Two most recent months for delta column
  const showDelta = ordered.length >= 2
  const prevLabel = ordered[ordered.length - 2]?.label

  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th className={styles.catCol}>Category</th>
            {ordered.map(mo => (
              <th key={mo.label} className={styles.amtCol}>
                {MONTH_LABEL(mo.label)}
              </th>
            ))}
            {showDelta && <th className={styles.deltaCol}>Change</th>}
          </tr>
        </thead>
        <tbody>
          {allCats.map(cat => {
            const prevTotal = lookup[prevLabel]?.[cat]?.total ?? 0
            const currTotal = lookup[latestLabel]?.[cat]?.total ?? 0
            const delta     = pctChange(prevTotal, currTotal)

            return (
              <tr key={cat} className={styles.row}>
                <td className={styles.catCell}>{cat}</td>
                {ordered.map(mo => {
                  const row = lookup[mo.label]?.[cat]
                  return (
                    <td key={mo.label} className={styles.amtCell}>
                      {row ? (
                        <span>
                          <span className={styles.amtValue}>{fmt(row.total)}</span>
                          <span className={styles.amtCount}>{row.count}×</span>
                        </span>
                      ) : (
                        <span className={styles.amtNone}>—</span>
                      )}
                    </td>
                  )
                })}
                {showDelta && (
                  <td className={styles.deltaCell}>
                    {delta === null ? (
                      <span className={styles.deltaNew}>new</span>
                    ) : (
                      <span
                        className={styles.deltaVal}
                        style={{
                          color: delta > 10
                            ? 'var(--red)'
                            : delta < -10
                              ? 'var(--green)'
                              : 'var(--muted)'
                        }}
                      >
                        {delta >= 0 ? '↑' : '↓'} {Math.abs(delta).toFixed(0)}%
                      </span>
                    )}
                  </td>
                )}
              </tr>
            )
          })}
        </tbody>
        <tfoot>
          <tr className={styles.totalRow}>
            <td className={styles.catCell}>Total spent</td>
            {ordered.map(mo => (
              <td key={mo.label} className={styles.amtCell}>
                <span className={styles.totalVal}>{fmt(mo.total_out)}</span>
              </td>
            ))}
            {showDelta && (() => {
              const prev = ordered[ordered.length - 2]?.total_out ?? 0
              const curr = ordered[ordered.length - 1]?.total_out ?? 0
              const d    = pctChange(prev, curr)
              return (
                <td className={styles.deltaCell}>
                  {d !== null && (
                    <span
                      className={styles.deltaVal}
                      style={{ color: d > 0 ? 'var(--red)' : 'var(--green)', fontWeight: 600 }}
                    >
                      {d >= 0 ? '↑' : '↓'} {Math.abs(d).toFixed(0)}%
                    </span>
                  )}
                </td>
              )
            })()}
          </tr>
        </tfoot>
      </table>
    </div>
  )
}

// ── Subcategory drill-down chart ────────────────────────────────────────────────
// TODO: move this panel into the month reporter / drill-down view once that
//       exists.  For now it lives here as a read-only panel — pick a month,
//       see every subcategory broken out as a bar, coloured by parent category.

const CAT_COLORS = {
  food: '#f59e0b', groceries: '#4ade80', transport: '#60a5fa',
  subscriptions: '#c084fc', shopping: '#f87171', health: '#34d399',
  utilities: '#fb923c', rent: '#e879f9', entertainment: '#a78bfa',
  self_care: '#f472b6', travel: '#38bdf8', cannabis: '#86efac',
  investment: '#6ee7b7', atm: '#fbbf24', fees: '#94a3b8',
  income: '#4ade80', transfer: '#64748b', other: '#475569',
}
const catColor = c => CAT_COLORS[c] ?? '#60a5fa'

function SubcatTooltip({ active, payload }) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  return (
    <div style={{
      background: '#1a1a1a', border: '1px solid #333', borderRadius: 6,
      padding: '8px 12px', fontFamily: 'var(--font-mono)', fontSize: 12,
    }}>
      <div style={{ color: catColor(d.category), marginBottom: 2 }}>{d.category}</div>
      <div style={{ color: '#e2e8f0' }}>{d.subcategory ?? '(uncategorised)'}</div>
      <div style={{ color: '#94a3b8' }}>${d.total.toFixed(2)} · {d.count} txns</div>
    </div>
  )
}

function SubcategoryChart({ months }) {
  const latestMonth = months[0]?.label  // months are newest-first from API
  const [selectedMonth, setSelectedMonth] = useState(latestMonth)
  const [subcats, setSubcats]             = useState([])
  const [loading, setLoading]             = useState(false)

  // keep selectedMonth in sync when months data changes
  useEffect(() => {
    if (latestMonth && !selectedMonth) setSelectedMonth(latestMonth)
  }, [latestMonth])

  useEffect(() => {
    if (!selectedMonth) return
    setLoading(true)
    api.getMonthlySubcats(selectedMonth)
      .then(rows => {
        // keep only rows that have a subcategory assigned
        setSubcats(rows.filter(r => r.subcategory))
      })
      .finally(() => setLoading(false))
  }, [selectedMonth])

  if (!latestMonth) return null

  // sort by total desc for a tidy chart
  const chartData = [...subcats].sort((a, b) => b.total - a.total)

  return (
    <div>
      {/* month picker */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <span style={{ color: 'var(--subtle)', fontSize: 11 }}>month</span>
        <select
          value={selectedMonth}
          onChange={e => setSelectedMonth(e.target.value)}
          style={{ fontSize: 12, background: '#1a1a1a', color: '#e2e8f0', border: '1px solid #333', borderRadius: 4, padding: '2px 6px' }}
        >
          {months.map(mo => (
            <option key={mo.label} value={mo.label}>{MONTH_LABEL(mo.label)}</option>
          ))}
        </select>
      </div>

      {loading ? (
        <p style={{ color: 'var(--subtle)', fontSize: 12 }}>Loading…</p>
      ) : chartData.length === 0 ? (
        <p style={{ color: 'var(--subtle)', fontSize: 12 }}>
          No subcategories assigned for this month yet — run AI or add correction rules.
        </p>
      ) : (
        <ResponsiveContainer width="100%" height={chartData.length * 28 + 20}>
          <BarChart
            data={chartData}
            layout="vertical"
            margin={{ top: 0, right: 16, left: 0, bottom: 0 }}
          >
            <XAxis type="number" hide />
            <YAxis
              type="category"
              dataKey="subcategory"
              width={130}
              tick={{ fill: 'var(--subtle)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip content={<SubcatTooltip />} cursor={{ fill: 'rgba(255,255,255,0.03)' }} />
            <Bar dataKey="total" radius={[0, 4, 4, 0]}>
              {chartData.map((entry, i) => (
                <Cell key={i} fill={catColor(entry.category)} fillOpacity={0.85} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}


// ── One-time charges section ───────────────────────────────────────────────────

function OneTimeSection({ months }) {
  // ordered oldest → newest (same as category table)
  const ordered = [...months].reverse()
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th className={styles.catCol}>Description</th>
            <th className={styles.catCol}>Category</th>
            {ordered.map(mo => (
              <th key={mo.label} className={styles.amtCol}>{MONTH_LABEL(mo.label)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {/* Collect all unique description+category combos across all months */}
          {(() => {
            const seen = new Map() // "desc|cat" → true
            const items = []
            for (const mo of ordered) {
              for (const it of (mo.one_time_items ?? [])) {
                const key = `${it.description}|${it.category}`
                if (!seen.has(key)) { seen.set(key, true); items.push(it) }
              }
            }
            return items.map(item => (
              <tr key={`${item.description}|${item.category}`} className={styles.row}>
                <td className={styles.catCell} style={{ color: 'var(--amber)' }}>{item.description}</td>
                <td className={styles.catCell}>{item.category}</td>
                {ordered.map(mo => {
                  const match = (mo.one_time_items ?? []).find(
                    x => x.description === item.description && x.category === item.category
                  )
                  return (
                    <td key={mo.label} className={styles.amtCell}>
                      {match
                        ? <span className={styles.amtValue}>{fmt(match.total)}</span>
                        : <span className={styles.amtNone}>—</span>
                      }
                    </td>
                  )
                })}
              </tr>
            ))
          })()}
        </tbody>
        <tfoot>
          <tr className={styles.totalRow}>
            <td className={styles.catCell} colSpan={2}>Total one-time</td>
            {ordered.map(mo => (
              <td key={mo.label} className={styles.amtCell}>
                {mo.one_time_out > 0
                  ? <span className={styles.totalVal} style={{ color: 'var(--amber)' }}>{fmt(mo.one_time_out)}</span>
                  : <span className={styles.amtNone}>—</span>
                }
              </td>
            ))}
          </tr>
        </tfoot>
      </table>
    </div>
  )
}


// ── Main view ──────────────────────────────────────────────────────────────────

export default function Monthly() {
  const [data,    setData]    = useState(null)
  const [loading, setLoading] = useState(true)
  const [limit,   setLimit]   = useState(6)

  useEffect(() => {
    setLoading(true)
    api.getMonthly(limit)
      .then(setData)
      .finally(() => setLoading(false))
  }, [limit])

  const months = data?.months ?? []

  return (
    <div className={styles.page}>
      {/* ── Header ── */}
      <div className={styles.header}>
        <div>
          <h1 className={styles.title}>// monthly</h1>
          <p className={styles.subtitle}>
            {months.length} months · newest first
          </p>
        </div>
        <select value={limit} onChange={e => setLimit(Number(e.target.value))}>
          <option value={3}>Last 3 months</option>
          <option value={6}>Last 6 months</option>
          <option value={12}>Last 12 months</option>
        </select>
      </div>

      {loading ? (
        <div className={styles.empty}>Loading…</div>
      ) : months.length === 0 ? (
        <div className={styles.empty}>No transactions imported yet.</div>
      ) : (
        <>
          {/* ── Month cards ── */}
          <div className={styles.cards}>
            {months.map((mo, i) => (
              <MonthCard key={mo.label} month={mo} isLatest={i === 0} />
            ))}
          </div>

          {/* ── Category table (regular spend only) ── */}
          <div className={styles.panel}>
            <div className={styles.panelTitle}>
              Regular spending by category
              <span className={styles.panelNote}>excludes one-time · matches burn rate</span>
            </div>
            <CategoryTable months={months} />
          </div>

          {/* ── Subcategory breakdown chart ── */}
          {/* TODO: move into month reporter drill-down once that view exists */}
          <div className={styles.panel}>
            <div className={styles.panelTitle}>
              Subcategory breakdown
              <span className={styles.panelNote}>assigned subcategories only · bars coloured by parent category</span>
            </div>
            <SubcategoryChart months={months} />
          </div>

          {/* ── One-time charges section ── */}
          {months.some(m => m.one_time_items?.length > 0) && (
            <div className={styles.panel}>
              <div className={styles.panelTitle} style={{ color: 'var(--amber)' }}>
                One-time charges
                <span className={styles.panelNote}>flagged as non-recurring · not counted in burn rate</span>
              </div>
              <OneTimeSection months={months} />
            </div>
          )}
        </>
      )}
    </div>
  )
}

import { useState, useEffect } from 'react'
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

function MonthCard({ month, isLatest }) {
  const netPositive = month.net >= 0
  const hasOneTime  = month.one_time_out > 0
  return (
    <div className={`${styles.monthCard} ${isLatest ? styles.monthCardLatest : ''}`}>
      <div className={styles.monthCardTitle}>{MONTH_LABEL(month.label)}</div>

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

import { useState, useEffect, useCallback } from 'react'
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts'
import { api } from '../api.js'
import styles from './Dashboard.module.css'

// ── Constants ───────────────────────────────────────────────────────────────────

const CAT_COLORS = {
  food:          '#f59e0b',
  groceries:     '#4ade80',
  transport:     '#60a5fa',
  subscriptions: '#c084fc',
  shopping:      '#f87171',
  health:        '#34d399',
  utilities:     '#fb923c',
  rent:          '#e879f9',
  entertainment: '#a78bfa',
  self_care:     '#f472b6',
  travel:        '#38bdf8',
  cannabis:      '#86efac',
  investment:    '#6ee7b7',
  atm:           '#fbbf24',
  fees:          '#94a3b8',
  income:        '#4ade80',
  insurance:     '#fca5a5',
  hobbies:       '#818cf8',
  other:         '#475569',
}

const catColor = c => CAT_COLORS[c] ?? '#60a5fa'

const fmt  = n => `$${Math.abs(n).toLocaleString('en-CA', { minimumFractionDigits: 0 })}`
const fmtd = n => `$${Math.abs(n).toLocaleString('en-CA', { minimumFractionDigits: 2 })}`

function monthLabel(ym) {
  const [y, m] = ym.split('-')
  return new Date(Number(y), Number(m) - 1).toLocaleString('en-CA', { month: 'long', year: 'numeric' })
}

// ── Stat card with month-over-month delta ────────────────────────────────────────

function StatCard({ label, value, sub, accent, curr, prev, deltaInvert }) {
  let delta = null
  if (prev && prev > 0) delta = ((curr - prev) / prev) * 100

  const up = delta > 0
  // deltaInvert: for SPENT, going up is bad (red). For INCOME/NET, going up is good (green).
  const deltaColor = delta === null
    ? null
    : (up !== !!deltaInvert) ? 'var(--green)' : 'var(--red)'

  return (
    <div className={styles.card}>
      <div className={styles.cardLabel}>{label}</div>
      <div className={styles.cardValue} style={{ color: accent }}>{value}</div>
      {sub && <div className={styles.cardSub}>{sub}</div>}
      {delta !== null && (
        <div className={styles.cardDelta} style={{ color: deltaColor }}>
          {up ? '▲' : '▼'} {Math.abs(delta).toFixed(0)}% vs prev
        </div>
      )}
    </div>
  )
}

// ── Expandable category bar chart ──────────────────────────────────────────────

function CategoryBreakdown({ categories }) {
  const [expanded, setExpanded] = useState(new Set())

  const toggle = cat =>
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(cat) ? next.delete(cat) : next.add(cat)
      return next
    })

  const maxTotal = Math.max(...categories.map(c => c.total), 1)

  return (
    <div className={styles.catList}>
      {categories.map(cat => {
        const isExpanded   = expanded.has(cat.category)
        const hasSubcats   = cat.subcategories?.some(s => s.subcategory)
        const pct          = (cat.total / maxTotal) * 100
        const delta        = cat.prev_total > 0
          ? ((cat.total - cat.prev_total) / cat.prev_total) * 100
          : null

        return (
          <div key={cat.category}>
            {/* ── Category row ── */}
            <div
              className={styles.catRow}
              onClick={() => hasSubcats && toggle(cat.category)}
              style={{ cursor: hasSubcats ? 'pointer' : 'default' }}
            >
              <span className={styles.catDot} style={{ background: catColor(cat.category) }} />
              <span className={styles.catName}>{cat.category}</span>
              <span className={styles.catAmt}>{fmt(cat.total)}</span>
              <div className={styles.barTrack}>
                <div
                  className={styles.barFill}
                  style={{ width: `${pct}%`, background: catColor(cat.category) }}
                />
              </div>
              {delta !== null && (
                <span
                  className={styles.catDelta}
                  style={{ color: delta > 10 ? 'var(--red)' : delta < -10 ? 'var(--green)' : 'var(--muted)' }}
                >
                  {delta >= 0 ? '▲' : '▼'} {Math.abs(delta).toFixed(0)}%
                </span>
              )}
              {hasSubcats && (
                <span className={styles.expandIcon}>{isExpanded ? '▲' : '▼'}</span>
              )}
            </div>

            {/* ── Subcategory rows ── */}
            {isExpanded && cat.subcategories?.filter(s => s.subcategory).map(sub => (
              <div key={sub.subcategory} className={styles.subcatRow}>
                <span className={styles.subcatIndent} />
                <span className={styles.subcatLine} style={{ background: catColor(cat.category) }} />
                <span className={styles.subcatName}>{sub.subcategory}</span>
                <span className={styles.subcatAmt}>{fmt(sub.total)}</span>
                <div className={styles.barTrack}>
                  <div
                    className={styles.barFill}
                    style={{
                      width: `${(sub.total / maxTotal) * 100}%`,
                      background: catColor(cat.category),
                      opacity: 0.45,
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
        )
      })}
    </div>
  )
}

// ── Fixed vs Variable donut ───────────────────────────────────────────────────

const DONUT_COLORS = ['#c084fc', '#60a5fa']

const DonutTooltip = ({ active, payload }) => {
  if (!active || !payload?.length) return null
  return (
    <div className={styles.tooltip}>
      <span style={{ color: payload[0].payload.fill }}>{payload[0].name}</span>
      <span> {fmtd(payload[0].value)}</span>
    </div>
  )
}

function FixedVariableDonut({ fixed, variable }) {
  const total = fixed + variable
  if (total <= 0) return <p className={styles.empty}>No data</p>

  const data = [
    { name: 'Fixed',    value: fixed,    fill: DONUT_COLORS[0] },
    { name: 'Variable', value: variable, fill: DONUT_COLORS[1] },
  ]

  return (
    <div className={styles.donutWrap}>
      <ResponsiveContainer width={160} height={160}>
        <PieChart>
          <Pie
            data={data}
            cx="50%"
            cy="50%"
            innerRadius={46}
            outerRadius={72}
            strokeWidth={0}
            dataKey="value"
          >
            {data.map((entry, i) => (
              <Cell key={i} fill={entry.fill} fillOpacity={0.85} />
            ))}
          </Pie>
          <Tooltip content={<DonutTooltip />} />
        </PieChart>
      </ResponsiveContainer>

      <div className={styles.donutLegend}>
        {data.map(d => (
          <div key={d.name} className={styles.donutLegendRow}>
            <span className={styles.donutDot} style={{ background: d.fill }} />
            <span className={styles.donutLegendLabel}>{d.name}</span>
            <span className={styles.donutLegendAmt} style={{ color: d.fill }}>
              {fmtd(d.value)}
            </span>
          </div>
        ))}
        <div className={styles.donutPct}>
          {Math.round((fixed / total) * 100)}% fixed
        </div>
      </div>
    </div>
  )
}

// ── Subscriptions + one-time charges panel ────────────────────────────────────

function FlagsPanel({ subscriptions, oneTime }) {
  if (subscriptions.length === 0 && oneTime.length === 0) return null

  return (
    <div className={styles.flagsPanel}>
      <div className={styles.panelTitle}>Subscriptions &amp; one-time charges</div>
      <div className={styles.flagsRow}>
        {subscriptions.map((s, i) => (
          <span key={i} className={styles.flagChip}>
            {s.description}
            <span className={styles.flagAmt}>{fmtd(s.total)}</span>
          </span>
        ))}
        {oneTime.map((o, i) => (
          <span key={i} className={`${styles.flagChip} ${styles.flagOneTime}`}>
            <span className={styles.flagTag}>one-time</span>
            {o.description} <span className={styles.flagAmt}>{fmtd(o.total)}</span>
          </span>
        ))}
      </div>
    </div>
  )
}

// ── AI Insights panel ─────────────────────────────────────────────────────────

const TYPE_ICON  = { warning: '⚠', tip: '→', info: '●' }
const TYPE_COLOR = { warning: 'var(--red)', tip: 'var(--amber)', info: 'var(--blue)' }

function InsightsPanel({ month }) {
  const [insights, setInsights] = useState(null)
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState(null)
  const [meta,     setMeta]     = useState(null)

  const refresh = useCallback(() => {
    setLoading(true)
    setError(null)
    api.postInsights(month)
      .then(data => {
        if (data.error) {
          setError(data.error)
        } else {
          setInsights(data.insights)
          setMeta({ backend: data.backend, model: data.model })
        }
      })
      .catch(err => setError(err.message ?? 'Failed to generate insights'))
      .finally(() => setLoading(false))
  }, [month])

  return (
    <div className={styles.insightsPanel}>
      <div className={styles.insightsHeader}>
        <span className={styles.panelTitle} style={{ marginBottom: 0 }}>AI Insights</span>
        {meta && (
          <span className={styles.insightsMeta}>
            {meta.backend} · {meta.model}
          </span>
        )}
        <button
          className={styles.refreshBtn}
          onClick={refresh}
          disabled={loading}
        >
          {loading ? '…' : '↻ Refresh'}
        </button>
      </div>

      {!insights && !loading && !error && (
        <p className={styles.insightsEmpty}>
          Click Refresh to generate AI insights for {month ? monthLabel(month) : 'this month'}.
        </p>
      )}
      {loading && (
        <p className={styles.insightsEmpty}>Generating insights…</p>
      )}
      {error && (
        <p className={styles.insightsError}>{error}</p>
      )}
      {insights && insights.length > 0 && (
        <ul className={styles.insightsList}>
          {insights.map((ins, i) => (
            <li key={i} className={styles.insightItem}>
              <span
                className={styles.insightIcon}
                style={{ color: TYPE_COLOR[ins.type] ?? TYPE_COLOR.info }}
              >
                {TYPE_ICON[ins.type] ?? '●'}
              </span>
              <span className={styles.insightText}>{ins.text}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// ── Main view ──────────────────────────────────────────────────────────────────

export default function Dashboard() {
  const [data,       setData]       = useState(null)
  const [month,      setMonth]      = useState(null)   // null = let API choose
  const [fetching,   setFetching]   = useState(true)   // true only on first load

  useEffect(() => {
    setFetching(true)
    api.getDashboard(month)
      .then(d => {
        setData(d)
        // lock to the month the API returned (important on first load)
        if (!month && d.month) setMonth(d.month)
      })
      .finally(() => setFetching(false))
  }, [month])

  // First load: nothing to show yet
  if (!data && fetching) return <div className={styles.loading}>Loading…</div>
  if (!data?.month) return <div className={styles.loading}>No transaction data found.</div>

  const { spent, income, net, runway_months, prev, txn_count } = data
  const netColor = net >= 0 ? 'var(--green)' : 'var(--red)'

  // Exclude non-spending categories from breakdown chart
  const spendCats = (data.categories ?? [])
    .filter(c => !['income', 'transfer', 'fees', 'refund'].includes(c.category))

  return (
    <div className={styles.page} style={{ opacity: fetching ? 0.6 : 1, transition: 'opacity 0.15s' }}>

      {/* ── Header ── */}
      <div className={styles.header}>
        <div>
          <h1 className={styles.title}>// dashboard</h1>
          <p className={styles.subtitle}>
            {monthLabel(data.month)} · {txn_count} transactions
          </p>
        </div>
        <select
          value={data.month}
          onChange={e => setMonth(e.target.value)}
          className={styles.monthPicker}
        >
          {(data.available_months ?? []).map(m => (
            <option key={m} value={m}>{monthLabel(m)}</option>
          ))}
        </select>
      </div>

      {/* ── Stat cards ── */}
      <div className={styles.cards}>
        <StatCard
          label="Spent"
          value={fmt(spent)}
          sub={data.is_current_month ? 'this month' : undefined}
          accent="var(--red)"
          curr={spent}
          prev={prev.spent}
          deltaInvert
        />
        <StatCard
          label="Income"
          value={fmt(income)}
          accent="var(--green)"
          curr={income}
          prev={prev.income}
        />
        <StatCard
          label="Net"
          value={(net >= 0 ? '+' : '') + fmt(net)}
          sub={net >= 0 ? 'ahead' : 'deficit'}
          accent={netColor}
        />
        <StatCard
          label="Runway"
          value={runway_months != null ? `${runway_months} mo` : '—'}
          sub="at current burn"
          accent="var(--amber)"
        />
      </div>

      {/* ── Category breakdown + Fixed/Variable ── */}
      <div className={styles.body}>
        <div className={styles.panel}>
          <div className={styles.panelTitle}>
            Category breakdown
            <span className={styles.panelNote}>click to expand subcategories</span>
          </div>
          {spendCats.length === 0
            ? <p className={styles.empty}>No spending data for this month.</p>
            : <CategoryBreakdown categories={spendCats} />
          }
        </div>

        <div className={styles.panel}>
          <div className={styles.panelTitle}>Fixed vs Variable</div>
          <FixedVariableDonut
            fixed={data.fixed_total ?? 0}
            variable={data.variable_total ?? 0}
          />
        </div>
      </div>

      {/* ── Subscriptions & one-time charges ── */}
      <FlagsPanel
        subscriptions={data.subscriptions ?? []}
        oneTime={data.one_time_charges ?? []}
      />

      {/* ── AI Insights ── */}
      <InsightsPanel month={data.month} />

    </div>
  )
}

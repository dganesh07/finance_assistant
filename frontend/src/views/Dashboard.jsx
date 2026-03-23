import { useState, useEffect } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, Cell,
} from 'recharts'
import { api } from '../api.js'
import styles from './Dashboard.module.css'

// ── Colour per category ────────────────────────────────────────────────────────
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
  transfer:      '#64748b',
  other:         '#475569',
}

const catColor = c => CAT_COLORS[c] ?? '#60a5fa'

// ── Sub-components ─────────────────────────────────────────────────────────────

function StatCard({ label, value, sub, accent }) {
  return (
    <div className={styles.card} style={{ borderColor: accent + '33' }}>
      <div className={styles.cardLabel}>{label}</div>
      <div className={styles.cardValue} style={{ color: accent }}>{value}</div>
      {sub && <div className={styles.cardSub}>{sub}</div>}
    </div>
  )
}

function BillRow({ bill }) {
  return (
    <div className={styles.billRow}>
      <span className={styles.billName}>{bill.name}</span>
      <span className={styles.billMeta}>
        {bill.due_day ? `due ${bill.due_day}` : bill.frequency}
      </span>
      <span
        className={styles.badge}
        style={bill.autopay
          ? { background: '#1a3a1a', color: '#4ade80' }
          : { background: '#292318', color: '#f59e0b' }}
      >
        {bill.autopay ? 'autopay' : 'manual'}
      </span>
      <span className={styles.billAmount}>${bill.amount.toFixed(2)}</span>
    </div>
  )
}

const CustomTooltip = ({ active, payload }) => {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  return (
    <div className={styles.tooltip}>
      <div className={styles.tooltipLabel}>{d.category}</div>
      <div className={styles.tooltipValue}>${d.total.toFixed(2)}</div>
      <div className={styles.tooltipSub}>{d.count} transactions</div>
    </div>
  )
}

// ── Main view ──────────────────────────────────────────────────────────────────

export default function Dashboard() {
  const [summary, setSummary]   = useState(null)
  const [bills,   setBills]     = useState([])
  const [loading, setLoading]   = useState(true)
  const [days,    setDays]      = useState(90)

  useEffect(() => {
    setLoading(true)
    Promise.all([api.getSummary(days), api.getBills()])
      .then(([s, b]) => { setSummary(s); setBills(b) })
      .finally(() => setLoading(false))
  }, [days])

  if (loading) return <div className={styles.loading}>Loading…</div>
  if (!summary) return <div className={styles.loading}>No data</div>

  const fmt = n => `$${Math.abs(n).toLocaleString('en-CA', { minimumFractionDigits: 2 })}`
  const netColor = summary.net >= 0 ? 'var(--green)' : 'var(--red)'

  // Exclude non-spending categories from the chart.
  // investment = GIC / savings transfers — real money moves but not discretionary spend.
  const chartData = (summary.by_category ?? [])
    .filter(c => !['income', 'transfer', 'fees', 'investment'].includes(c.category))
    .sort((a, b) => b.total - a.total)

  const totalBills = bills.reduce((s, b) => s + b.amount, 0)
  const manualBills = bills.filter(b => !b.autopay)

  return (
    <div className={styles.page}>
      {/* ── Header ── */}
      <div className={styles.header}>
        <div>
          <h1 className={styles.title}>// dashboard</h1>
          <p className={styles.subtitle}>
            {summary.period} · from {summary.period_start}
          </p>
        </div>
        <select value={days} onChange={e => setDays(Number(e.target.value))}>
          <option value={30}>Last 30 days</option>
          <option value={60}>Last 60 days</option>
          <option value={90}>Last 90 days</option>
          <option value={180}>Last 6 months</option>
          <option value={365}>Last year</option>
        </select>
      </div>

      {/* ── Stat cards ── */}
      <div className={styles.cards}>
        <StatCard
          label="Total Spent"
          value={fmt(summary.total_out)}
          accent="var(--red)"
        />
        <StatCard
          label="Total In"
          value={fmt(summary.total_in)}
          accent="var(--green)"
        />
        <StatCard
          label="Net"
          value={(summary.net >= 0 ? '+' : '-') + fmt(summary.net)}
          sub={summary.net >= 0 ? 'ahead' : 'deficit'}
          accent={netColor}
        />
        <StatCard
          label="Runway"
          value={summary.runway_months != null ? `${summary.runway_months} mo` : '—'}
          sub="at current burn rate"
          accent="var(--amber)"
        />
      </div>

      {/* ── Chart + bills side by side ── */}
      <div className={styles.body}>

        {/* Spending breakdown */}
        <div className={styles.panel}>
          <div className={styles.panelTitle}>Spending by category</div>
          {chartData.length === 0 ? (
            <p className={styles.empty}>No spending data for this period.</p>
          ) : (
            <ResponsiveContainer width="100%" height={chartData.length * 36 + 20}>
              <BarChart
                data={chartData}
                layout="vertical"
                margin={{ top: 0, right: 16, left: 0, bottom: 0 }}
              >
                <XAxis type="number" hide />
                <YAxis
                  type="category"
                  dataKey="category"
                  width={110}
                  tick={{ fill: 'var(--subtle)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip content={<CustomTooltip />} cursor={{ fill: 'rgba(255,255,255,0.03)' }} />
                <Bar dataKey="total" radius={[0, 4, 4, 0]}>
                  {chartData.map((entry) => (
                    <Cell key={entry.category} fill={catColor(entry.category)} fillOpacity={0.85} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>

        {/* Bills */}
        <div className={styles.panel}>
          <div className={styles.panelTitle}>
            Bills
            <span className={styles.panelMeta}>${totalBills.toFixed(2)}/mo</span>
          </div>
          {bills.length === 0
            ? <p className={styles.empty}>No bills configured in bills.json</p>
            : bills.map((b, i) => <BillRow key={i} bill={b} />)
          }
          {manualBills.length > 0 && (
            <div className={styles.manualAlert}>
              ⚠ Manual payment needed:{' '}
              {manualBills.map(b => b.name).join(', ')}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

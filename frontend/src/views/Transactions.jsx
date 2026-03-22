import { useState, useEffect, useCallback } from 'react'
import { api } from '../api.js'
import styles from './Transactions.module.css'

/*
 * Transactions view — browse all transactions with search + filters.
 * Click a row's category badge to edit it inline.
 */

export default function Transactions() {
  const [data,       setData]       = useState({ total: 0, transactions: [] })
  const [categories, setCategories] = useState([])
  const [loading,    setLoading]    = useState(true)

  // Filters
  const [search,    setSearch]    = useState('')
  const [category,  setCategory]  = useState('')
  const [dateFrom,  setDateFrom]  = useState('')
  const [dateTo,    setDateTo]    = useState('')

  // Inline edit state: { id, category }
  const [editing, setEditing] = useState(null)

  const load = useCallback(() => {
    setLoading(true)
    api.getTransactions({ search, category, date_from: dateFrom, date_to: dateTo, limit: 200 })
      .then(setData)
      .finally(() => setLoading(false))
  }, [search, category, dateFrom, dateTo])

  useEffect(() => { load() }, [load])
  useEffect(() => { api.getCategories().then(setCategories) }, [])

  // Save inline edit
  const saveEdit = async (id) => {
    if (!editing || editing.id !== id) return
    await api.updateTransaction(id, { category: editing.category })
    setEditing(null)
    load()
  }

  return (
    <div className={styles.page}>
      {/* ── Header ── */}
      <div className={styles.header}>
        <div>
          <h1 className={styles.title}>// transactions</h1>
          <p className={styles.subtitle}>{data.total} rows</p>
        </div>
      </div>

      {/* ── Filters ── */}
      <div className={styles.filters}>
        <input
          type="text"
          placeholder="Search description…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          className={styles.searchInput}
        />
        <select value={category} onChange={e => setCategory(e.target.value)}>
          <option value="">All categories</option>
          {categories.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <input
          type="date"
          value={dateFrom}
          onChange={e => setDateFrom(e.target.value)}
          title="From date"
        />
        <input
          type="date"
          value={dateTo}
          onChange={e => setDateTo(e.target.value)}
          title="To date"
        />
        <button
          className={styles.clearBtn}
          onClick={() => { setSearch(''); setCategory(''); setDateFrom(''); setDateTo('') }}
        >
          Clear
        </button>
      </div>

      {/* ── Table ── */}
      {loading ? (
        <div className={styles.empty}>Loading…</div>
      ) : data.transactions.length === 0 ? (
        <div className={styles.empty}>No transactions match your filters.</div>
      ) : (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Date</th>
                <th>Description</th>
                <th className={styles.right}>Amount</th>
                <th>Account</th>
                <th>Category</th>
                <th>Subcategory</th>
                <th className={styles.center}>Confirmed</th>
              </tr>
            </thead>
            <tbody>
              {data.transactions.map(txn => {
                const isDebit  = txn.type === 'debit'
                const amtColor = isDebit ? 'var(--red)' : 'var(--green)'
                const isEditing = editing?.id === txn.id

                return (
                  <tr key={txn.id} className={styles.row}>
                    <td className={styles.date}>{txn.date}</td>
                    <td className={styles.desc} title={txn.description}>
                      {txn.description}
                    </td>
                    <td className={styles.right} style={{ color: amtColor, fontFamily: 'var(--font-mono)' }}>
                      {isDebit ? '-' : '+'}${txn.amount.toFixed(2)}
                    </td>
                    <td className={styles.account}>{txn.account}</td>
                    <td>
                      {isEditing ? (
                        <div className={styles.inlineEdit}>
                          <select
                            autoFocus
                            value={editing.category}
                            onChange={e => setEditing({ id: txn.id, category: e.target.value })}
                            onKeyDown={e => { if (e.key === 'Enter') saveEdit(txn.id); if (e.key === 'Escape') setEditing(null) }}
                          >
                            {categories.map(c => <option key={c} value={c}>{c}</option>)}
                          </select>
                          <button className={styles.saveBtn} onClick={() => saveEdit(txn.id)}>Save</button>
                          <button className={styles.cancelBtn} onClick={() => setEditing(null)}>×</button>
                        </div>
                      ) : (
                        <span
                          className={styles.catBadge}
                          onClick={() => setEditing({ id: txn.id, category: txn.category })}
                          title="Click to edit"
                        >
                          {txn.category}
                        </span>
                      )}
                    </td>
                    <td className={styles.sub}>{txn.subcategory ?? '—'}</td>
                    <td className={styles.center}>
                      {txn.confirmed
                        ? <span style={{ color: 'var(--green)' }}>✓</span>
                        : <span style={{ color: 'var(--muted)' }}>—</span>
                      }
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {data.total > 200 && (
            <p className={styles.limitNote}>Showing first 200 of {data.total} rows.</p>
          )}
        </div>
      )}
    </div>
  )
}

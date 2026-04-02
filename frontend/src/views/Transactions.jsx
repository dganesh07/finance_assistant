import { useState, useEffect, useCallback } from 'react'
import { api } from '../api.js'
import styles from './Transactions.module.css'

/*
 * Transactions view — browse all transactions with search + filters.
 * Click a row's category/subcategory badge to edit inline.
 * Click 1× to toggle one-time flag (excluded from burn rate).
 */

const PAGE_SIZE = 75

export default function Transactions() {
  const [data,           setData]           = useState({ total: 0, transactions: [] })
  const [categories,     setCategories]     = useState([])
  const [subcategoryMap, setSubcategoryMap] = useState({})
  const [availMonths,    setAvailMonths]    = useState([])
  const [loading,        setLoading]        = useState(true)

  // Filters
  const [search,    setSearch]    = useState('')
  const [category,  setCategory]  = useState('')
  const [dateFrom,  setDateFrom]  = useState('')
  const [dateTo,    setDateTo]    = useState('')
  const [monthPick, setMonthPick] = useState('')

  // Pagination
  const [page, setPage] = useState(0)

  // Inline edit state: { id, category, subcategory, notes }
  const [editing,     setEditing]     = useState(null)
  // Note-only edit state: { id, value }
  const [noteEditing, setNoteEditing] = useState(null)
  // Inline action feedback
  const [saving,      setSaving]      = useState(false)  // true while a PATCH is in flight
  const [saveError,   setSaveError]   = useState(null)
  const [loadError,   setLoadError]   = useState(null)

  useEffect(() => { setPage(0) }, [search, category, dateFrom, dateTo, monthPick])

  const load = useCallback(() => {
    setLoading(true)
    setLoadError(null)
    let from = dateFrom, to = dateTo
    if (monthPick) {
      const [y, m] = monthPick.split('-').map(Number)
      from = `${monthPick}-01`
      to   = `${monthPick}-${String(new Date(y, m, 0).getDate()).padStart(2, '0')}`
    }
    api.getTransactions({ search, category, date_from: from, date_to: to,
                          limit: PAGE_SIZE, offset: page * PAGE_SIZE })
      .then(setData)
      .catch(e => setLoadError(e.message ?? 'Failed to load transactions'))
      .finally(() => setLoading(false))
  }, [search, category, dateFrom, dateTo, monthPick, page])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    api.getCategories().then(setCategories)
    api.getSubcategories().then(setSubcategoryMap)
    api.getMonthly(24).then(d => setAvailMonths((d.months ?? []).map(m => m.label)))
  }, [])

  // ── Inline edit ─────────────────────────────────────────────────────────────
  const startEdit = txn =>
    setEditing({ id: txn.id, category: txn.category, subcategory: txn.subcategory ?? '', notes: txn.notes ?? '' })

  const saveEdit = async (id) => {
    if (!editing || editing.id !== id) return
    // If the chosen category has no subcategories, always clear it
    const subAllowed = subcategoryMap[editing.category] ?? []
    const sub = subAllowed.length > 0 ? (editing.subcategory || null) : null
    const notes = editing.notes.trim() || null
    setSaving(true)
    setSaveError(null)
    try {
      await api.updateTransaction(id, { category: editing.category, subcategory: sub, notes })
      // Patch local state directly — avoids re-fetch which resets scroll position
      setData(prev => ({
        ...prev,
        transactions: prev.transactions.map(t =>
          t.id === id ? { ...t, category: editing.category, subcategory: sub, notes } : t
        ),
      }))
      setEditing(null)
    } catch (e) {
      setSaveError(e.message ?? 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  // ── Note-only edit ──────────────────────────────────────────────────────────
  const startNoteEdit = (txn) =>
    setNoteEditing({ id: txn.id, value: txn.notes ?? '' })

  const saveNote = async () => {
    if (!noteEditing) return
    const notes = noteEditing.value.trim() || null
    setSaving(true)
    setSaveError(null)
    try {
      await api.updateTransaction(noteEditing.id, { notes })
      setData(prev => ({
        ...prev,
        transactions: prev.transactions.map(t =>
          t.id === noteEditing.id ? { ...t, notes } : t
        ),
      }))
      setNoteEditing(null)
    } catch (e) {
      setSaveError(e.message ?? 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  // ── One-time toggle ─────────────────────────────────────────────────────────
  const toggleOneTime = async (txn) => {
    const next = txn.is_one_time ? 0 : 1
    setSaveError(null)
    try {
      await api.updateTransaction(txn.id, { is_one_time: next })
    } catch (e) {
      setSaveError(e.message ?? 'Update failed')
      return
    }
    setData(prev => ({
      ...prev,
      transactions: prev.transactions.map(t =>
        t.id === txn.id ? { ...t, is_one_time: next } : t
      ),
    }))
  }

  return (
    <div className={styles.page}>
      {/* ── Header ── */}
      <div className={styles.header}>
        <div>
          <h1 className={styles.title}>// transactions</h1>
          <p className={styles.subtitle}>
            {data.total} rows
            {data.total > PAGE_SIZE && (
              <span style={{ marginLeft: 8 }}>
                · showing {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, data.total)}
              </span>
            )}
          </p>
        </div>
      </div>

      {/* ── Filters ── */}
      <div className={styles.filters}>
        <input type="text" placeholder="Search description…" value={search}
          onChange={e => setSearch(e.target.value)} className={styles.searchInput} />
        <select value={category} onChange={e => setCategory(e.target.value)}>
          <option value="">All categories</option>
          {categories.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <select value={monthPick}
          onChange={e => { setMonthPick(e.target.value); setDateFrom(''); setDateTo('') }}
          className={styles.monthInput}>
          <option value="">All months</option>
          {availMonths.map(m => {
            const [y, mo] = m.split('-')
            const label = new Date(y, mo - 1).toLocaleString('en-CA', { month: 'short', year: 'numeric' })
            return <option key={m} value={m}>{label}</option>
          })}
        </select>
        <input type="date" value={dateFrom}
          onChange={e => { setDateFrom(e.target.value); setMonthPick('') }} title="From date" />
        <input type="date" value={dateTo}
          onChange={e => { setDateTo(e.target.value); setMonthPick('') }} title="To date" />
        <button className={styles.clearBtn}
          onClick={() => { setSearch(''); setCategory(''); setDateFrom('');
                           setDateTo(''); setMonthPick(''); setPage(0) }}>
          Clear
        </button>
      </div>

      {/* ── Error / saving feedback ── */}
      {loadError && <div className={styles.empty} style={{ color: 'var(--red)' }}>Error: {loadError}</div>}
      {saveError && <div className={styles.empty} style={{ color: 'var(--red)', marginBottom: 0 }}>Save failed: {saveError}</div>}

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
                <th className={styles.center}>Note</th>
                <th className={styles.center}>✓</th>
                <th className={styles.center} title="One-time — excluded from burn rate">1×</th>
              </tr>
            </thead>
            <tbody>
              {data.transactions.map(txn => {
                const isDebit   = txn.type === 'debit'
                const amtColor  = isDebit ? 'var(--red)' : 'var(--green)'
                const isEditing = editing?.id === txn.id
                const subOptions = subcategoryMap[isEditing ? editing.category : txn.category] ?? []

                return (
                  <tr key={txn.id} className={styles.row}>
                    <td className={styles.date}>{txn.date}</td>
                    <td className={styles.desc} title={txn.description}>{txn.description}</td>
                    <td className={styles.right} style={{ color: amtColor, fontFamily: 'var(--font-mono)' }}>
                      {isDebit ? '-' : '+'}${txn.amount.toFixed(2)}
                    </td>
                    <td className={styles.account}>{txn.account}</td>

                    {/* ── Category ── */}
                    <td>
                      {isEditing ? (
                        <select autoFocus value={editing.category}
                          onChange={e => setEditing(p => ({ ...p, category: e.target.value, subcategory: '' }))}
                          onKeyDown={e => { if (e.key === 'Enter') saveEdit(txn.id); if (e.key === 'Escape') setEditing(null) }}>
                          {categories.map(c => <option key={c} value={c}>{c}</option>)}
                        </select>
                      ) : (
                        <span className={styles.catBadge} onClick={() => startEdit(txn)} title="Click to edit">
                          {txn.category}
                        </span>
                      )}
                    </td>

                    {/* ── Subcategory ── */}
                    <td className={styles.sub}>
                      {isEditing ? (
                        subOptions.length > 0 ? (
                          <select value={editing.subcategory}
                            onChange={e => setEditing(p => ({ ...p, subcategory: e.target.value }))}
                            className={styles.subSelect}
                            onKeyDown={e => { if (e.key === 'Enter') saveEdit(txn.id); if (e.key === 'Escape') setEditing(null) }}>
                            <option value="">— none —</option>
                            {subOptions.map(s => <option key={s} value={s}>{s}</option>)}
                          </select>
                        ) : editing.subcategory ? (
                          // Stale subcategory from a previous category — show it so user can clear it
                          <span className={styles.staleSub}>
                            {editing.subcategory}
                            <button onClick={() => setEditing(p => ({ ...p, subcategory: '' }))} title="Clear subcategory">×</button>
                          </span>
                        ) : (
                          <span style={{ color: 'var(--muted)', fontSize: 10 }}>—</span>
                        )
                      ) : txn.subcategory ? (
                        <span className={styles.subBadge} onClick={() => startEdit(txn)} title="Click to edit">
                          {txn.subcategory}
                        </span>
                      ) : (
                        <span className={styles.subBadgeEmpty} onClick={() => startEdit(txn)} title="Click to add">—</span>
                      )}
                    </td>

                    {/* ── Notes ── */}
                    <td className={styles.center}>
                      {isEditing ? (
                        // Part of full category edit — input in the notes field
                        <input
                          type="text"
                          value={editing.notes}
                          onChange={e => setEditing(p => ({ ...p, notes: e.target.value }))}
                          onKeyDown={e => { if (e.key === 'Enter') saveEdit(txn.id); if (e.key === 'Escape') setEditing(null) }}
                          placeholder="note…"
                          className={styles.noteInput}
                        />
                      ) : noteEditing?.id === txn.id ? (
                        // Standalone note edit
                        <input
                          type="text"
                          value={noteEditing.value}
                          onChange={e => setNoteEditing(p => ({ ...p, value: e.target.value }))}
                          onBlur={saveNote}
                          onKeyDown={e => { if (e.key === 'Enter') saveNote(); if (e.key === 'Escape') setNoteEditing(null) }}
                          placeholder="note…"
                          className={styles.noteInput}
                          autoFocus
                        />
                      ) : txn.notes ? (
                        <span className={styles.noteIconWrap} data-note={txn.notes} onClick={() => startNoteEdit(txn)} style={{ cursor: 'pointer' }}>
                          <span className={styles.noteIcon}>note</span>
                        </span>
                      ) : (
                        <span className={styles.noteEmpty} onClick={() => startNoteEdit(txn)} title="Add note">+</span>
                      )}
                    </td>

                    {/* ── Confirmed / Save ── */}
                    <td className={styles.center}>
                      {isEditing ? (
                        <button className={styles.saveBtn} onClick={() => saveEdit(txn.id)} disabled={saving} title="Save">
                          {saving ? '…' : '✓'}
                        </button>
                      ) : txn.confirmed ? (
                        <span style={{ color: 'var(--green)' }}>✓</span>
                      ) : (
                        <span style={{ color: 'var(--muted)' }}>—</span>
                      )}
                    </td>

                    {/* ── One-time toggle / Cancel ── */}
                    <td className={styles.center}>
                      {isEditing ? (
                        <button className={styles.cancelBtn} onClick={() => setEditing(null)} title="Cancel">×</button>
                      ) : (
                        <button
                          className={txn.is_one_time ? styles.oneTimeOn : styles.oneTimeOff}
                          onClick={() => toggleOneTime(txn)}
                          title={txn.is_one_time ? 'One-time — click to unmark' : 'Mark as one-time'}>
                          1×
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          {/* ── Pagination ── */}
          {data.total > PAGE_SIZE && (() => {
            const totalPages = Math.ceil(data.total / PAGE_SIZE)
            return (
              <div className={styles.pagination}>
                <button className={styles.pageBtn} onClick={() => setPage(0)} disabled={page === 0}>«</button>
                <button className={styles.pageBtn} onClick={() => setPage(p => p - 1)} disabled={page === 0}>‹</button>
                <span className={styles.pageInfo}>{page + 1} / {totalPages}</span>
                <button className={styles.pageBtn} onClick={() => setPage(p => p + 1)} disabled={page >= totalPages - 1}>›</button>
                <button className={styles.pageBtn} onClick={() => setPage(totalPages - 1)} disabled={page >= totalPages - 1}>»</button>
              </div>
            )
          })()}
        </div>
      )}
    </div>
  )
}

import { useState, useEffect, useCallback } from 'react'
import { api } from '../api.js'
import styles from './Review.module.css'

/*
 * Review view — approve, correct, and pin transaction categories.
 *
 * Workflow:
 *   ① Import    — parse PDFs in data/statements/, save raw rows (unknown, confirmed=0)
 *   ② Apply Rules — apply corrections.json instantly, no LLM
 *   ③ Run AI    — send remaining unknowns to Ollama (slow, optional)
 *   ④ Confirm All — approve everything at once, or confirm row-by-row
 *
 * Per-row actions:
 *   • Change dropdown  → pick correct category (turns amber)
 *   • "save as rule"   → writes to corrections.json so this merchant is never wrong again
 *   • "pin AI guess"   → same, but when AI was already correct
 *   • ✓ button         → confirms the row and moves it to Confirmed tab
 */

// ── Categorization cheat-sheet rules ───────────────────────────────────────────
const CHEAT_RULES = [
  {
    group: 'Transfers (not fees or expenses)',
    color: 'var(--blue)',
    rules: [
      { match: 'Credit card payment',       hint: 'PREAUTHORIZEDPAYMENT / TDVISAPREAUTHPYMT',  cat: 'transfer' },
      { match: 'Account-to-account move',   hint: 'large round amount between your own accounts', cat: 'transfer' },
      { match: 'Questrade deposit',         hint: 'QUESTRADEINC MSP',                           cat: 'investment' },
    ],
  },
  {
    group: 'Credits & refunds',
    color: 'var(--green)',
    rules: [
      { match: 'Merchant refund / return',  hint: 'positive (credit) amount from a store',      cat: 'refund' },
      { match: 'Received from work / EI',   hint: 'payroll or government deposit',              cat: 'income → work' },
      { match: 'Transfer from savings',     hint: 'money moved from EQ / TFSA into chequing',  cat: 'transfer' },
    ],
  },
  {
    group: 'Easy mix-ups',
    color: 'var(--amber)',
    rules: [
      { match: 'Costco',                    hint: 'bulk grocery store',                         cat: 'groceries' },
      { match: 'Amazon / AMZN',             hint: 'marketplace purchases',                      cat: 'shopping' },
      { match: 'COMPASS card',              hint: 'TransLink transit top-up',                   cat: 'transport → transit' },
      { match: 'IMPARK / NW PARKING / COV', hint: 'parking lots',                               cat: 'transport → parking' },
      { match: 'ATM cash withdrawal',       hint: 'bank machine withdrawal',                    cat: 'atm' },
    ],
  },
]

function CheatSheet() {
  const [open, setOpen] = useState(false)

  return (
    <div className={styles.cheatSheet}>
      <button className={styles.cheatToggle} onClick={() => setOpen(o => !o)}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, marginRight: 4 }}>
          {open ? '▾' : '▸'}
        </span>
        categorization cheat sheet
        {!open && <span className={styles.cheatHint}>— quirky rules to remember</span>}
      </button>

      {open && (
        <div className={styles.cheatBody}>
          {CHEAT_RULES.map(group => (
            <div key={group.group} className={styles.cheatGroup}>
              <div className={styles.cheatGroupLabel} style={{ color: group.color }}>
                {group.group}
              </div>
              {group.rules.map(r => (
                <div key={r.match} className={styles.cheatRule}>
                  <span className={styles.cheatMatch}>{r.match}</span>
                  <span className={styles.cheatArrow}>→</span>
                  <span className={styles.cheatCat}>{r.cat}</span>
                  <span className={styles.cheatNote}>{r.hint}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function Review({ onConfirm }) {
  const [tab,                setTab]                = useState('needs_review')
  const [transactions,       setTransactions]       = useState([])
  const [categories,         setCategories]         = useState([])
  const [subcategoryMap,     setSubcategoryMap]     = useState({}) // category → [subcategory]
  const [localCats,          setLocalCats]          = useState({}) // id → chosen category (pending save)
  const [localSubs,          setLocalSubs]          = useState({}) // id → chosen subcategory (pending save)
  const [saveAsRule,         setSaveAsRule]          = useState({}) // id → bool (save as correction rule)
  const [loading,            setLoading]            = useState(true)
  const [jobStatus,          setJobStatus]          = useState(null) // null | 'running' | 'done' | 'error'
  const [jobResult,          setJobResult]          = useState(null)
  const [parseStatus,        setParseStatus]        = useState(null) // null | 'running' | 'done' | 'error'
  const [parseResult,        setParseResult]        = useState(null)
  const [corrStatus,         setCorrStatus]         = useState(null) // null | 'running' | 'done'
  const [corrResult,         setCorrResult]         = useState(null)
  const [lastImportFiles,    setLastImportFiles]    = useState([]) // source_files from last import
  const [confirmedLocalCats, setConfirmedLocalCats] = useState({}) // id → category for confirmed tab edits
  const [autoOpen,           setAutoOpen]           = useState(true) // collapsible open state

  const load = useCallback(() => {
    setLoading(true)

    let txnPromise
    if (tab === 'needs_review') {
      txnPromise = api.getReviewTransactions()
    } else if (lastImportFiles.length > 0) {
      txnPromise = api.getTransactions({ confirmed: 1, source_file: lastImportFiles, limit: 500 })
        .then(r => r.transactions)
    } else {
      // No session import yet — fall back to the most recent batch from DB
      txnPromise = api.getSourceFiles().then(sourceFiles => {
        if (!sourceFiles.length) return []
        const latestDay = sourceFiles[0].latest.split('T')[0]
        const recentFiles = sourceFiles
          .filter(f => f.latest.startsWith(latestDay))
          .map(f => f.source_file)
        setLastImportFiles(recentFiles)
        return api.getTransactions({ confirmed: 1, source_file: recentFiles, limit: 500 })
          .then(r => r.transactions)
      })
    }

    Promise.all([txnPromise, api.getCategories(), api.getSubcategories()])
      .then(([txns, cats, subMap]) => {
        setTransactions(txns)
        setCategories(cats)
        setSubcategoryMap(subMap)
        const seed = {}
        txns.forEach(t => { seed[t.id] = t.category ?? 'other' })
        if (tab === 'confirmed') setConfirmedLocalCats(seed)
        else setLocalCats(seed)
      })
      .finally(() => setLoading(false))
  }, [tab, lastImportFiles])

  useEffect(() => { load() }, [load])

  // ── Confirm a single row ────────────────────────────────────────────────────
  const confirmOne = async (txn) => {
    // Effective category: what the user picked, or the AI's suggestion.
    // localCats[id] is only set once the user touches the dropdown.
    const effectiveCat = localCats[txn.id] ?? txn.category

    // Block confirm if category is still unresolved
    if (!effectiveCat || effectiveCat === 'unknown') {
      alert('Please pick a category before confirming.')
      return
    }

    const effectiveSub = (localSubs[txn.id] ?? txn.subcategory) || null

    // Save as permanent correction rule if toggled (works for AI guesses too)
    if (saveAsRule[txn.id]) {
      await api.addCorrection(txn.description, effectiveCat, effectiveSub)
    }

    await api.updateTransaction(txn.id, {
      category:    effectiveCat,
      subcategory: effectiveSub,
      confirmed:   1,
    })
    setTransactions(prev => prev.filter(t => t.id !== txn.id))
    onConfirm?.()
  }

  // ── Confirm all visible rows ────────────────────────────────────────────────
  const confirmAll = async () => {
    // First apply any local category changes, then bulk-confirm
    await Promise.all(
      transactions.map(t =>
        localCats[t.id] && localCats[t.id] !== t.category
          ? api.updateTransaction(t.id, { category: localCats[t.id] })
          : Promise.resolve()
      )
    )
    await api.confirmAll(transactions.map(t => t.id))
    setTransactions([])
    onConfirm?.()
  }

  // ── Import statements (parse files in data/statements/) ────────────────────
  const importStatements = async () => {
    setParseStatus('running')
    setParseResult(null)
    try {
      const result = await api.parseStatements()
      setParseResult(result)
      setParseStatus('done')
      const importedFiles = result.files.filter(f => f.inserted > 0).map(f => f.file)
      if (importedFiles.length > 0) setLastImportFiles(importedFiles)
      load()
      onConfirm?.()
    } catch (e) {
      setParseStatus('error')
      setParseResult({ error: e.message })
    }
  }

  // ── Apply corrections.json (fast, no LLM) ───────────────────────────────────
  const applyCorrections = async () => {
    setCorrStatus('running')
    setCorrResult(null)
    const result = await api.applyCorrections()
    setCorrResult(result)
    setCorrStatus('done')
    load()
    onConfirm?.()
  }

  // ── Run AI categorizer (slow, Ollama) ───────────────────────────────────────
  const runCategorizer = async () => {
    setJobStatus('running')
    setJobResult(null)
    const { job_id } = await api.runCategorizer()

    // Poll every 1.5s until done
    const poll = setInterval(async () => {
      const job = await api.getJob(job_id)
      if (job.status === 'done') {
        clearInterval(poll)
        setJobStatus('done')
        setJobResult(job)
        load() // refresh the table
      } else if (job.status === 'error') {
        clearInterval(poll)
        setJobStatus('error')
        setJobResult(job)
      }
    }, 1500)
  }

  // ── Save an override on an auto-confirmed row ───────────────────────────────
  const saveConfirmedEdit = async (txn) => {
    const newCat = confirmedLocalCats[txn.id]
    await api.updateTransaction(txn.id, { category: newCat, confirmed: 1 })
    setTransactions(prev => prev.map(t => t.id === txn.id ? { ...t, category: newCat } : t))
    setConfirmedLocalCats(p => ({ ...p, [txn.id]: newCat }))
  }

  const pending = transactions.filter(t => !t.confirmed)

  return (
    <div className={styles.page}>
      {/* ── Header ── */}
      <div className={styles.header}>
        <div>
          <h1 className={styles.title}>// review</h1>
          <p className={styles.subtitle}>Approve or correct AI-suggested categories</p>
        </div>

        {tab === 'needs_review' && (
          <div className={styles.actions}>
            {/* Step 1: Import */}
            <button
              className={styles.btnStep}
              onClick={importStatements}
              disabled={parseStatus === 'running'}
              title="Scan data/statements/ for new PDFs/CSVs and save to DB"
            >
              {parseStatus === 'running' ? 'Importing…' : '① Import'}
            </button>

            {/* Step 2: Apply corrections (fast, no LLM) */}
            <button
              className={styles.btnStep}
              onClick={applyCorrections}
              disabled={corrStatus === 'running'}
              title="Apply corrections.json rules to all unknown transactions — instant, no AI"
            >
              {corrStatus === 'running' ? 'Applying…' : '② Apply Rules'}
            </button>

            {/* Step 3: AI categorizer (optional, slow) */}
            <button
              className={styles.btnSecondary}
              onClick={runCategorizer}
              disabled={jobStatus === 'running'}
              title="Run Ollama AI on remaining unknowns — slow, optional"
            >
              {jobStatus === 'running' ? 'AI running…' : '③ Run AI'}
            </button>

            {/* Step 4: Confirm All */}
            <button
              className={styles.btnPrimary}
              onClick={confirmAll}
              disabled={transactions.length === 0}
              title="Mark all visible transactions as confirmed"
            >
              ④ Confirm All ({transactions.length})
            </button>
          </div>
        )}
      </div>

      {/* ── Cheat sheet ── */}
      {tab === 'needs_review' && <CheatSheet />}

      {/* ── Parse status banner ── */}
      {parseStatus === 'running' && (
        <div className={styles.banner} style={{ borderColor: 'var(--blue)', color: 'var(--blue)' }}>
          Scanning data/statements/ for new files…
        </div>
      )}
      {parseStatus === 'done' && parseResult && (
        <>
          <div className={styles.banner} style={{ borderColor: 'var(--green)', color: 'var(--green)' }}>
            {parseResult.files_processed === 0
              ? 'No new files found — all statements already imported.'
              : <>
                  {`Imported ${parseResult.total_inserted} of ${parseResult.files.reduce((s, f) => s + f.inserted + f.skipped, 0)} parsed rows across ${parseResult.files_processed} file(s).`}
                  {parseResult.total_skipped > 0 && (
                    <span style={{ color: 'var(--blue)', marginLeft: 8 }}>
                      {parseResult.total_skipped} already in DB (skipped).
                    </span>
                  )}
                  <ul style={{ margin: '6px 0 0', paddingLeft: 20, fontSize: 12, lineHeight: 1.8 }}>
                    {parseResult.files.filter(f => f.inserted + f.skipped > 0 || f.status === 'imported').map(f => (
                      <li key={f.file} style={{ fontFamily: 'var(--font-mono)', color: 'var(--text)' }}>
                        {f.file}
                        <span style={{ color: 'var(--green)', marginLeft: 8 }}>+{f.inserted} new</span>
                        {f.skipped > 0 && <span style={{ color: 'var(--blue)', marginLeft: 6 }}>{f.skipped} skipped</span>}
                        {f.dropped > 0 && <span style={{ color: 'var(--amber)', marginLeft: 6 }}>⚠ {f.dropped} dropped</span>}
                      </li>
                    ))}
                  </ul>
                </>
            }
            {parseResult.total_dropped > 0 && (
              <span style={{ color: 'var(--amber)', marginLeft: 8 }}>
                ⚠ {parseResult.total_dropped} row(s) dropped — check terminal.
              </span>
            )}
          </div>

          {/* Outlier warnings */}
          {parseResult.total_outliers > 0 && (
            <div className={styles.banner} style={{ borderColor: 'var(--red)', color: 'var(--amber)' }}>
              <strong style={{ color: 'var(--red)' }}>
                ⚠ {parseResult.total_outliers} outlier amount(s) flagged — possible merge artifact:
              </strong>
              <ul style={{ margin: '6px 0 0', paddingLeft: 20, fontSize: 12, lineHeight: 1.8 }}>
                {parseResult.files.flatMap(f =>
                  (f.outlier_warnings || []).map((w, i) => (
                    <li key={`${f.file}-${i}`}>
                      <span style={{ fontFamily: 'var(--font-mono)' }}>
                        {w.date} — {w.description} —{' '}
                        <span style={{ color: 'var(--red)' }}>${w.amount.toLocaleString('en-CA', { minimumFractionDigits: 2 })}</span>
                      </span>
                      <span style={{ color: 'var(--fg-muted)', marginLeft: 8 }}>{w.reason}</span>
                    </li>
                  ))
                )}
              </ul>
            </div>
          )}

          {/* Reconciliation results */}
          {parseResult.files.some(f => f.reconciliation) && (
            <div className={styles.banner} style={{ borderColor: 'var(--border)' }}>
              <strong style={{ color: 'var(--text)' }}>Balance reconciliation:</strong>
              <ul style={{ margin: '6px 0 0', paddingLeft: 20, fontSize: 12, lineHeight: 1.8 }}>
                {parseResult.files.filter(f => f.reconciliation).map(f => {
                  const r = f.reconciliation
                  const ok = r.ok
                  return (
                    <li key={f.file} style={{ color: ok ? 'var(--green)' : 'var(--red)' }}>
                      {ok ? '✓' : '✗'} {f.file}
                      {r.parsed_net !== undefined
                        ? ` — net ${r.parsed_net >= 0 ? '+' : ''}$${Math.abs(r.parsed_net).toLocaleString('en-CA', { minimumFractionDigits: 2 })} (statement: ${r.expected_net >= 0 ? '+' : ''}$${Math.abs(r.expected_net).toLocaleString('en-CA', { minimumFractionDigits: 2 })})`
                        : ` — charges $${(r.parsed_charges||0).toLocaleString('en-CA', { minimumFractionDigits: 2 })} / expected $${(r.expected_charges||0).toLocaleString('en-CA', { minimumFractionDigits: 2 })}`
                      }
                      {!ok && <span style={{ color: 'var(--red)', marginLeft: 6 }}>Δ ${r.delta ?? r.delta_charges} — investigate</span>}
                    </li>
                  )
                })}
              </ul>
            </div>
          )}
        </>
      )}
      {parseStatus === 'error' && (
        <div className={styles.banner} style={{ borderColor: 'var(--red)', color: 'var(--red)' }}>
          Import error: {parseResult?.error ?? 'unknown'}
        </div>
      )}

      {/* ── Apply corrections banner ── */}
      {corrStatus === 'running' && (
        <div className={styles.banner} style={{ borderColor: 'var(--blue)', color: 'var(--blue)' }}>
          Applying correction rules…
        </div>
      )}
      {corrStatus === 'done' && corrResult && (
        <div className={styles.banner} style={{ borderColor: 'var(--green)', color: 'var(--green)' }}>
          {corrResult.applied === 0
            ? `No matches — ${corrResult.remaining_unknown} transaction(s) still need a category.`
            : `Applied ${corrResult.applied} rule match(es). ${corrResult.remaining_unknown} still unknown.`
          }
        </div>
      )}

      {/* ── AI job status banner ── */}
      {jobStatus === 'running' && (
        <div className={styles.banner} style={{ borderColor: 'var(--amber)', color: 'var(--amber)' }}>
          Ollama is categorizing… this may take a minute.
        </div>
      )}
      {jobStatus === 'done' && jobResult && (
        <div
          className={styles.banner}
          style={{
            borderColor: jobResult.processed === 0 ? 'var(--border)' : 'var(--green)',
            color:       jobResult.processed === 0 ? 'var(--muted)' : 'var(--green)',
          }}
        >
          {jobResult.processed === 0
            ? 'No unknowns left — AI had nothing to do. Fix wrong categories in the dropdown above.'
            : `AI done — ${jobResult.categorized}/${jobResult.processed} transactions categorized.`
          }
        </div>
      )}
      {jobStatus === 'error' && (
        <div className={styles.banner} style={{ borderColor: 'var(--red)', color: 'var(--red)' }}>
          AI error: {jobResult?.error ?? 'unknown'}. Is Ollama running?
        </div>
      )}

      {/* ── Tabs ── */}
      <div className={styles.tabs}>
        <button
          className={tab === 'needs_review' ? styles.tabActive : styles.tab}
          onClick={() => setTab('needs_review')}
        >
          Needs Review
          {pending.length > 0 && (
            <span className={styles.tabBadge}>{transactions.length}</span>
          )}
        </button>
        <button
          className={tab === 'confirmed' ? styles.tabActive : styles.tab}
          onClick={() => setTab('confirmed')}
        >
          Confirmed
        </button>
      </div>

      {/* ── Table ── */}
      {tab === 'confirmed' ? (
        <AutoConfirmedSection
          transactions={transactions}
          categories={categories}
          subcategoryMap={subcategoryMap}
          localCats={confirmedLocalCats}
          onCatChange={(id, cat) => setConfirmedLocalCats(p => ({ ...p, [id]: cat }))}
          onSave={saveConfirmedEdit}
          lastImportFiles={lastImportFiles}
          autoOpen={autoOpen}
          onToggle={() => setAutoOpen(p => !p)}
          loading={loading}
        />
      ) : loading ? (
        <div className={styles.empty}>Loading…</div>
      ) : transactions.length === 0 ? (
        <div className={styles.empty}>
          ✓ Nothing to review — run the categorizer or import a statement.
        </div>
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
                <th className={styles.center}>Confirm</th>
              </tr>
            </thead>
            <tbody>
              {transactions.map(txn => (
                <ReviewRow
                  key={txn.id}
                  txn={txn}
                  categories={categories}
                  subcategoryMap={subcategoryMap}
                  localCat={localCats[txn.id] ?? txn.category}
                  localSub={localSubs[txn.id] ?? txn.subcategory ?? ''}
                  onCatChange={cat => setLocalCats(p => ({ ...p, [txn.id]: cat }))}
                  onSubChange={sub => setLocalSubs(p => ({ ...p, [txn.id]: sub }))}
                  saveAsRule={saveAsRule[txn.id] ?? false}
                  onSaveAsRuleChange={v => setSaveAsRule(p => ({ ...p, [txn.id]: v }))}
                  onConfirm={() => confirmOne(txn)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── Single row (needs review) ───────────────────────────────────────────────────

function ReviewRow({ txn, categories, subcategoryMap, localCat, localSub, onCatChange, onSubChange, saveAsRule, onSaveAsRuleChange, onConfirm }) {
  const isDebit    = txn.type === 'debit'
  const amtColor   = isDebit ? 'var(--red)' : 'var(--green)'
  const sign       = isDebit ? '-' : '+'
  const isUnpicked = !localCat || localCat === 'unknown'
  const changed    = !isUnpicked && localCat !== txn.category
  const aiHasGuess = txn.category && txn.category !== 'unknown'
  const ruleLabel  = isUnpicked ? null
    : (changed || !aiHasGuess) ? 'save as rule'
    : 'pin AI guess as rule'

  // Subcategory options for the currently selected category
  const subOptions = (subcategoryMap[localCat] ?? [])

  return (
    <tr className={styles.row}>
      <td className={styles.date}>{txn.date}</td>
      <td className={styles.desc} title={txn.description}>{txn.description}</td>
      <td className={styles.right} style={{ color: amtColor, fontFamily: 'var(--font-mono)' }}>
        {sign}${txn.amount.toFixed(2)}
      </td>
      <td className={styles.account}>{txn.account}</td>
      <td>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {/* Category picker */}
          <select
            value={isUnpicked ? '' : localCat}
            onChange={e => { onCatChange(e.target.value); onSubChange('') /* reset sub when cat changes */ }}
            className={changed ? styles.selectChanged : isUnpicked ? styles.selectUnpicked : styles.select}
          >
            {isUnpicked && <option value="" disabled>— pick a category —</option>}
            {categories.map(c => <option key={c} value={c}>{c}</option>)}
          </select>

          {/* Subcategory picker — always shown when category has defined subcategories */}
          {!isUnpicked && subOptions.length > 0 && (
            <select
              value={localSub}
              onChange={e => onSubChange(e.target.value)}
              className={styles.select}
              style={{ fontSize: 11, opacity: 0.85 }}
            >
              <option value="">— subcategory (optional) —</option>
              {subOptions.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          )}

          {/* Save as rule toggle */}
          {ruleLabel && (
            <label className={styles.ruleToggle}>
              <input
                type="checkbox"
                checked={saveAsRule}
                onChange={e => onSaveAsRuleChange(e.target.checked)}
              />
              {ruleLabel}
            </label>
          )}
        </div>
      </td>
      <td className={styles.center}>
        <button className={styles.confirmBtn} onClick={onConfirm} title="Confirm this category">
          ✓
        </button>
      </td>
    </tr>
  )
}

// ── Auto-confirmed section (confirmed tab) ─────────────────────────────────────

function AutoConfirmedSection({ transactions, categories, subcategoryMap, localCats, onCatChange, onSave, lastImportFiles, autoOpen, onToggle, loading }) {
  if (loading) return <div className={styles.empty}>Loading…</div>

  return (
    <div>
      {lastImportFiles.length > 0 && (
        <div className={styles.importMeta}>
          <span style={{ color: 'var(--muted)' }}>Last import:</span>
          {lastImportFiles.map(f => (
            <span key={f} className={styles.fileName}>{f}</span>
          ))}
        </div>
      )}

      <div className={styles.collapsibleHeader} onClick={onToggle}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }}>{autoOpen ? '▾' : '▸'}</span>
        <span>Auto-confirmed by rules</span>
        <span className={styles.collapsibleCount}>({transactions.length})</span>
      </div>

      {autoOpen && (
        transactions.length === 0 ? (
          <div className={styles.empty}>No confirmed transactions from this import.</div>
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
                  <th className={styles.center}>Override</th>
                </tr>
              </thead>
              <tbody>
                {transactions.map(txn => {
                  const localCat = localCats[txn.id] ?? txn.category
                  const changed  = localCat !== txn.category
                  const isDebit  = txn.type === 'debit'
                  const sub      = txn.subcategory
                  return (
                    <tr key={txn.id} className={styles.row}>
                      <td className={styles.date}>{txn.date}</td>
                      <td className={styles.desc} title={txn.description}>{txn.description}</td>
                      <td className={styles.right} style={{ color: isDebit ? 'var(--red)' : 'var(--green)', fontFamily: 'var(--font-mono)' }}>
                        {isDebit ? '-' : '+'}${txn.amount.toFixed(2)}
                      </td>
                      <td className={styles.account}>{txn.account}</td>
                      <td>
                        <select
                          value={localCat}
                          onChange={e => onCatChange(txn.id, e.target.value)}
                          className={changed ? styles.selectChanged : styles.select}
                        >
                          {categories.map(c => <option key={c} value={c}>{c}</option>)}
                        </select>
                      </td>
                      <td>
                        {sub
                          ? <span className={styles.catBadge} style={{ opacity: 0.7 }}>{sub}</span>
                          : <span style={{ color: 'var(--muted)', fontSize: 10, fontFamily: 'var(--font-mono)' }}>—</span>
                        }
                      </td>
                      <td className={styles.center}>
                        {changed && (
                          <button className={styles.confirmBtn} onClick={() => onSave(txn)} title="Save override">
                            ✓
                          </button>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )
      )}
    </div>
  )
}

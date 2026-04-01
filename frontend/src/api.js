/*
 * api.js — thin wrapper around fetch() for the Finance Agent API.
 *
 * All functions return a Promise that resolves to parsed JSON.
 * The base URL comes from the VITE_API_URL env variable (set in .env)
 * and defaults to http://localhost:8000 for local development.
 */

const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

// Throw on non-2xx so callers get a real error instead of a silent JSON parse of an error body.
const _check = r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r }

const get  = url       => fetch(`${BASE}${url}`).then(_check).then(r => r.json())
const post = (url, body) =>
  fetch(`${BASE}${url}`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(body),
  }).then(_check).then(r => r.json())

const patch = (url, body) =>
  fetch(`${BASE}${url}`, {
    method:  'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(body),
  }).then(_check).then(r => r.json())

export const api = {
  // Summary + stats
  getSummary:            (days = 60)    => get(`/api/summary?days=${days}`),
  getMonthly:            (months = 12)  => get(`/api/monthly?months=${months}`),
  getMonthlySubcats:     (month)        => get(`/api/monthly-subcategories?month=${month}`),
  getSpendingPeriods:    ()             => get('/api/spending-periods'),

  // Categories + subcategories (from config.py)
  getCategories:    () => get('/api/categories'),
  getSubcategories: () => get('/api/subcategories'),

  // Bills from bills.json
  getBills: () => get('/api/bills'),

  // Transactions
  getReviewTransactions: () => get('/api/transactions/review'),
  getTransactions: (params = {}) => {
    const qs = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => {
      if (v == null || v === '') return
      if (Array.isArray(v)) v.forEach(item => qs.append(k, item))
      else qs.append(k, v)
    })
    const str = qs.toString()
    return get(`/api/transactions${str ? '?' + str : ''}`)
  },

  // Update a single transaction (category, confirmed, notes)
  updateTransaction: (id, body) => patch(`/api/transactions/${id}`, body),

  // Manually create a transaction
  addTransaction: (body) => post('/api/transactions', body),

  // Confirm multiple transactions at once
  confirmAll: (ids) => post('/api/transactions/confirm-all', { ids }),

  // Distinct account names
  getAccounts: () => get('/api/accounts'),

  // Corrections-only pass (fast, no LLM)
  applyCorrections: () => post('/api/apply-corrections', {}),

  // Background AI categorizer (slow, Ollama)
  runCategorizer: () => post('/api/run-categorizer', {}),
  getJob: (jobId)  => get(`/api/job/${jobId}`),

  // Statement import
  listStatements:   () => get('/api/statements'),
  parseStatements:  () => post('/api/parse-statements', {}),
  getSourceFiles:   () => get('/api/source-files'),

  // Hybrid dashboard
  getDashboard: (month) => get(`/api/dashboard${month ? `?month=${month}` : ''}`),

  // AI insights (POST triggers generation)
  postInsights: (month) => post(`/api/insights${month ? `?month=${month}` : ''}`, {}),

  // Portfolio — Google Sheets read-only (accounts + investment holdings)
  getPortfolio: () => get('/api/portfolio'),

  // Correction rules
  getCorrections:    () => get('/api/corrections'),
  addCorrection:     (key, category, subcategory = null) =>
    post('/api/corrections', { key, category, subcategory }),
  deleteCorrection:  (key) => {
    return fetch(`${BASE}/api/corrections/${encodeURIComponent(key)}`, {
      method: 'DELETE',
    }).then(_check).then(r => r.json())
  },
}

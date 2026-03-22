/*
 * api.js — thin wrapper around fetch() for the Finance Agent API.
 *
 * All functions return a Promise that resolves to parsed JSON.
 * The base URL comes from the VITE_API_URL env variable (set in .env)
 * and defaults to http://localhost:8000 for local development.
 */

const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

const get  = url       => fetch(`${BASE}${url}`).then(r => r.json())
const post = (url, body) =>
  fetch(`${BASE}${url}`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(body),
  }).then(r => r.json())

const patch = (url, body) =>
  fetch(`${BASE}${url}`, {
    method:  'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(body),
  }).then(r => r.json())

export const api = {
  // Summary + stats
  getSummary: (days = 60) => get(`/api/summary?days=${days}`),

  // Categories list (from config.py)
  getCategories: () => get('/api/categories'),

  // Bills from bills.json
  getBills: () => get('/api/bills'),

  // Transactions
  getReviewTransactions: () => get('/api/transactions/review'),
  getTransactions: (params = {}) => {
    const qs = new URLSearchParams(
      Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
    ).toString()
    return get(`/api/transactions${qs ? '?' + qs : ''}`)
  },

  // Update a single transaction (category, confirmed, notes)
  updateTransaction: (id, body) => patch(`/api/transactions/${id}`, body),

  // Bulk confirm
  confirmAll: (ids) => post('/api/transactions/confirm-all', { ids }),

  // Corrections-only pass (fast, no LLM)
  applyCorrections: () => post('/api/apply-corrections', {}),

  // Background AI categorizer (slow, Ollama)
  runCategorizer: () => post('/api/run-categorizer', {}),
  getJob: (jobId)  => get(`/api/job/${jobId}`),

  // Statement import
  listStatements:   () => get('/api/statements'),
  parseStatements:  () => post('/api/parse-statements', {}),

  // Correction rules
  getCorrections:    () => get('/api/corrections'),
  addCorrection:     (key, category, subcategory = null) =>
    post('/api/corrections', { key, category, subcategory }),
  deleteCorrection:  (key) => {
    return fetch(`${BASE}/api/corrections/${encodeURIComponent(key)}`, {
      method: 'DELETE',
    }).then(r => r.json())
  },
}

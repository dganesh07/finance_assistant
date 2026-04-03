// Canonical category color map — matches config.py CATEGORIES order.
// Single source of truth: import here instead of defining locally per view.
export const CAT_COLORS = {
  groceries:     '#4ade80',  // green
  food:          '#f59e0b',  // amber
  lifestyle:     '#a78bfa',  // violet
  shopping:      '#f87171',  // red
  self_care:     '#f472b6',  // pink
  hobbies:       '#818cf8',  // indigo
  health:        '#34d399',  // teal
  subscriptions: '#c084fc',  // purple
  transport:     '#60a5fa',  // blue
  travel:        '#38bdf8',  // sky
  utilities:     '#fb923c',  // orange
  rent:          '#e879f9',  // magenta
  income:        '#4ade80',  // green
  refund:        '#2dd4bf',  // cyan
  transfer:      '#64748b',  // slate
  investment:    '#6ee7b7',  // mint
  insurance:     '#fca5a5',  // rose
  atm:           '#fbbf24',  // yellow
  fees:          '#94a3b8',  // cool-grey
  other:         '#475569',  // dark slate
}

export const catColor = c => CAT_COLORS[c] ?? '#60a5fa'

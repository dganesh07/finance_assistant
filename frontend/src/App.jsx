import { useState, useEffect } from 'react'
import Sidebar from './components/Sidebar.jsx'
import Dashboard from './views/Dashboard.jsx'
import Review from './views/Review.jsx'
import Transactions from './views/Transactions.jsx'
import { api } from './api.js'
import styles from './App.module.css'

export default function App() {
  const [view, setView] = useState('dashboard')
  const [reviewCount, setReviewCount] = useState(0)

  // Fetch the unconfirmed count so the sidebar badge stays up to date.
  // Any view can call refreshReviewCount() after confirming transactions.
  const refreshReviewCount = () => {
    api.getSummary().then(d => setReviewCount(d.review_count ?? 0)).catch(() => {})
  }

  useEffect(() => { refreshReviewCount() }, [])

  return (
    <div className={styles.layout}>
      <Sidebar view={view} setView={setView} reviewCount={reviewCount} />
      <main className={styles.main}>
        {view === 'dashboard'     && <Dashboard />}
        {view === 'review'        && <Review onConfirm={refreshReviewCount} />}
        {view === 'transactions'  && <Transactions />}
      </main>
    </div>
  )
}

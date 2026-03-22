import styles from './Sidebar.module.css'

const NAV = [
  { id: 'dashboard',    label: 'Dashboard',     icon: '▦' },
  { id: 'review',       label: 'Review',         icon: '◈' },
  { id: 'transactions', label: 'Transactions',   icon: '≡' },
]

export default function Sidebar({ view, setView, reviewCount }) {
  return (
    <aside className={styles.sidebar}>
      {/* Branding */}
      <div className={styles.brand}>
        <span className={styles.brandMono}>// finance</span>
        <span className={styles.brandSub}>agent</span>
      </div>

      {/* Nav */}
      <nav className={styles.nav}>
        {NAV.map(item => (
          <button
            key={item.id}
            className={view === item.id ? styles.linkActive : styles.link}
            onClick={() => setView(item.id)}
          >
            <span className={styles.icon}>{item.icon}</span>
            <span>{item.label}</span>
            {item.id === 'review' && reviewCount > 0 && (
              <span className={styles.badge}>{reviewCount}</span>
            )}
          </button>
        ))}
      </nav>

      {/* Footer hint */}
      <div className={styles.footer}>
        <div className={styles.footerLine}>
          <span className={styles.dot} style={{ background: 'var(--green)' }} />
          API localhost:8000
        </div>
        <div className={styles.footerLine}>
          <span className={styles.dot} style={{ background: 'var(--blue)' }} />
          UI  localhost:5173
        </div>
      </div>
    </aside>
  )
}

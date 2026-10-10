export const STATUS_LABEL = { good: 'Good', medium: 'Medium', 'at-risk': 'At risk' }
export const TYPE_LABEL = { rope: 'Ropes', sheave: 'Sheave', bearing: 'Bearing', traction: 'Traction' }

export const fmt = {
  num: (v, d = 0) => (v == null || Number.isNaN(v) ? '-' : Number(v).toLocaleString('en-IN', { maximumFractionDigits: d, minimumFractionDigits: d })),
  inr: (v) => (v == null ? '-' : `₹${Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`),
  pct: (v, d = 0) => (v == null ? '-' : `${Number(v).toFixed(d)}%`),
  rul: (days) => {
    if (days == null) return '-'
    if (days >= 730) return `${(days / 365).toFixed(1)} yr`
    if (days >= 60) return `${Math.round(days / 30.4)} mo`
    return `${Math.round(days)} d`
  },
  date: (iso) => (iso ? new Date(iso).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) : '-'),
  dateShort: (iso) => (iso ? new Date(iso).toLocaleDateString('en-IN', { day: '2-digit', month: 'short' }) : ''),
  dateTime: (iso) => (iso ? new Date(iso).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '-'),
  ago: (iso) => {
    if (!iso) return '-'
    const s = (Date.now() - new Date(iso).getTime()) / 1000
    const a = Math.abs(s)
    const span = a < 60 ? null : a < 3600 ? `${Math.round(a / 60)} min` : a < 86400 ? `${Math.round(a / 3600)} h` : `${Math.round(a / 86400)} d`
    if (!span) return 'just now'
    return s >= 0 ? `${span} ago` : `in ${span}`
  },
}

export function healthColor(h) {
  if (h == null) return 'var(--text-muted)'
  if (h < 45) return 'var(--critical)'
  if (h < 72) return 'var(--warning)'
  return 'var(--good)'
}

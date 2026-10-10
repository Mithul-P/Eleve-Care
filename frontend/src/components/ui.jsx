import { IconAlert, IconCheck, IconX } from '../icons'
import { STATUS_LABEL, healthColor } from '../format'

// Status is never colour alone: icon + label + colour.
export function StatusBadge({ status }) {
  const Icon = status === 'good' ? IconCheck : status === 'medium' ? IconAlert : IconX
  return (
    <span className={`badge ${status || 'neutral'}`}>
      <Icon />
      {STATUS_LABEL[status] || status || 'Unknown'}
    </span>
  )
}

export function HealthMeter({ value, width }) {
  return (
    <div className="meter" style={{ width }} role="meter" aria-valuenow={value ?? 0} aria-valuemin={0} aria-valuemax={100}>
      <span style={{ width: `${Math.max(2, value ?? 0)}%`, background: healthColor(value) }} />
    </div>
  )
}

export function HealthRing({ value, size = 92, label = 'Health' }) {
  const r = (size - 10) / 2
  const c = 2 * Math.PI * r
  const v = Math.max(0, Math.min(100, value ?? 0))
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`${label} ${Math.round(v)} of 100`}>
      <circle cx={size / 2} cy={size / 2} r={r} stroke="var(--surface-3)" strokeWidth="8" fill="none" />
      <circle cx={size / 2} cy={size / 2} r={r} stroke={healthColor(value)} strokeWidth="8" fill="none"
        strokeDasharray={`${(v / 100) * c} ${c}`} strokeLinecap="round" transform={`rotate(-90 ${size / 2} ${size / 2})`} />
      <text x="50%" y="48%" textAnchor="middle" dominantBaseline="middle" fill="var(--text-primary)" fontSize={size * 0.27} fontWeight="600">{value == null ? '-' : Math.round(v)}</text>
      <text x="50%" y="70%" textAnchor="middle" fill="var(--text-muted)" fontSize={size * 0.12}>{label}</text>
    </svg>
  )
}

export function Stat({ label, value, unit, foot, color }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value" style={color ? { color } : undefined}>{value}{unit && <span className="unit">{unit}</span>}</div>
      {foot && <div className="stat-foot">{foot}</div>}
    </div>
  )
}

export function Seg({ value, options, onChange, label }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} className={value === o.value ? 'on' : ''} aria-pressed={value === o.value} onClick={() => onChange(o.value)}>{o.label}</button>
      ))}
    </div>
  )
}

/** Recharts tooltip: value leads, series name follows, line key in the series colour. */
export function ChartTooltip({ active, payload, label, labelFormatter, valueFormatter }) {
  if (!active || !payload || !payload.length) return null
  return (
    <div className="chart-tooltip">
      <div className="tt-title">{labelFormatter ? labelFormatter(label, payload) : label}</div>
      {payload.filter((p) => p.value != null).map((p) => (
        <div className="tt-row" key={p.dataKey}>
          <span className="tt-key" style={{ background: p.color || p.stroke || p.fill }} />
          <span className="tt-val">{valueFormatter ? valueFormatter(p.value, p) : p.value}</span>
          <span className="tt-name">{p.name}</span>
        </div>
      ))}
    </div>
  )
}

export function Loading({ what = 'Loading' }) {
  return <div className="empty">{what}…</div>
}

export function ErrorBox({ error }) {
  if (!error) return null
  return <div className="error">{error.message || String(error)}</div>
}

export const axisProps = {
  stroke: 'var(--grid)',
  tickLine: false,
  axisLine: { stroke: 'var(--grid)' },
  tick: { fill: 'var(--text-muted)', fontSize: 11 },
}

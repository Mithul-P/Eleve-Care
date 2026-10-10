// Fleet-wide materials list (spec section 2/8): every component of every
// elevator in one table, so a person can see what's used where - and what
// still needs filling in - without opening each elevator's own Materials &
// specification page. Editing still happens on the component (or per-
// elevator) page; this page is the index that gets you there.
import { useState } from 'react'
import { Link } from 'react-router-dom'
import BackBar from '../components/BackBar'
import { ErrorBox, HealthMeter, Loading, Stat } from '../components/ui'
import { TYPE_LABEL, fmt } from '../format'
import { useLiveFetch } from '../live'

export default function Materials() {
  const { data, error } = useLiveFetch('/analytics/materials')
  const [onlyIncomplete, setOnlyIncomplete] = useState(false)
  const [q, setQ] = useState('')

  const trail = [{ label: 'Elevators', to: '/elevators' }, { label: 'Materials' }]
  if (error && !data) return (
    <>
      <BackBar trail={trail} />
      <ErrorBox error={error} />
    </>
  )
  if (!data) return (
    <>
      <BackBar trail={trail} />
      <Loading />
    </>
  )

  const needle = q.trim().toLowerCase()
  const rows = data.components.filter((r) => {
    if (onlyIncomplete && r.completeness_pct >= 100) return false
    if (!needle) return true
    return [r.elevator_name, r.component_name, r.material_label, TYPE_LABEL[r.component_type]]
      .some((v) => (v || '').toLowerCase().includes(needle))
  })

  return (
    <>
      <BackBar trail={trail} />
      <div className="page-head">
        <div>
          <h1>Materials</h1>
          <div className="sub">Material specification of every component across the fleet - click a row to view or change it.</div>
        </div>
      </div>

      <div className="grid g4" style={{ marginBottom: 16 }}>
        <Stat label="Components tracked" value={data.components.length} />
        <Stat label="Fleet completeness (avg)" value={data.overall_completeness_pct} unit="%" />
        <Stat label="Incomplete specs" value={data.incomplete_count} foot="below 100% complete"
          color={data.incomplete_count ? 'var(--warning)' : undefined} />
        <Stat label="Elevators covered" value={new Set(data.components.map((r) => r.elevator_id)).size} />
      </div>

      <div className="row" style={{ marginBottom: 12, flexWrap: 'wrap', gap: 12, alignItems: 'center' }}>
        <input
          placeholder="Search elevator, component or material…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ maxWidth: 320 }}
        />
        <label className="atrisk-filter">
          <input type="checkbox" checked={onlyIncomplete} onChange={(e) => setOnlyIncomplete(e.target.checked)} />
          Show only incomplete
        </label>
        <span className="small muted">{rows.length} of {data.components.length} shown</span>
      </div>

      <div className="card" style={{ padding: 0 }}>
        <div className="scroll-x">
          <table>
            <thead>
              <tr>
                <th>Elevator</th>
                <th>Component</th>
                <th>Type</th>
                <th>Material</th>
                <th className="num">Completeness</th>
                <th>Last updated</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr><td colSpan={7} className="empty">No components match this filter.</td></tr>
              )}
              {rows.map((r) => (
                <tr key={r.component_id}>
                  <td><Link to={`/elevators/${r.elevator_id}`}>{r.elevator_name}</Link></td>
                  <td>{r.component_name}</td>
                  <td>{TYPE_LABEL[r.component_type] || r.component_type}</td>
                  <td>{r.material_label || <span className="muted small">Not specified</span>}</td>
                  <td className="num" style={{ minWidth: 140 }}>
                    <div className="row small" style={{ justifyContent: 'flex-end', gap: 8 }}>
                      <span className="mono">{r.completeness_pct}%</span>
                      <div style={{ width: 70 }}><HealthMeter value={r.completeness_pct} /></div>
                    </div>
                  </td>
                  <td className="small muted">{r.updated_at ? fmt.date(r.updated_at) : '—'}</td>
                  <td><Link className="btn ghost sm" to={`/components/${r.component_id}#materials`}>View / change</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}

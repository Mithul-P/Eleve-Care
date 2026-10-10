import { Link } from 'react-router-dom'
import { HealthMeter, Loading, Stat, StatusBadge, ErrorBox } from '../components/ui'
import { TYPE_LABEL, fmt, healthColor } from '../format'
import { useLiveFetch } from '../live'

export default function Elevators() {
  const { data, error } = useLiveFetch('/elevators')
  if (error && !data) return <ErrorBox error={error} />
  if (!data) return <Loading />
  const comps = data.flatMap((e) => Object.entries(e.component_counts || {}))
  const count = (s) => comps.filter(([k]) => k === s).reduce((a, [, v]) => a + v, 0)
  const avg = data.reduce((a, e) => a + (e.health_score || 0), 0) / Math.max(1, data.length)
  const complaints = data.reduce((a, e) => a + e.open_complaints, 0)
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Elevator fleet</h1>
          <div className="sub">Suspension health across {data.length} elevators - ropes, sheave, bearings and traction machine.</div>
        </div>
      </div>
      <div className="grid g4" style={{ marginBottom: 16 }}>
        <Stat label="Fleet health (avg)" value={fmt.num(avg)} unit="/100" color={healthColor(avg)} />
        <Stat label="Components at risk" value={count('at-risk')} foot="failure likely within 30 days" color={count('at-risk') ? 'var(--critical)' : undefined} />
        <Stat label="Needing attention" value={count('medium')} foot="medium status - watch closely" />
        <Stat label="Open complaints" value={complaints} foot="from public reports" />
      </div>
      <div className="grid g3">
        {data.map((e) => (
          <Link key={e.id} to={`/elevators/${e.id}`} className={`card elev-card ${e.status}`}>
            <div className="title-row">
              <div style={{ minWidth: 0 }}>
                <div className="row" style={{ gap: 8 }}>
                  <span className="name">{e.name}</span>
                  {e.is_demo && <span className="demo-tag">Demo</span>}
                </div>
                <div className="small muted">{e.building} · {e.location}</div>
              </div>
              <div style={{ marginLeft: 'auto' }}><StatusBadge status={e.status} /></div>
            </div>
            <div>
              <div className="row small" style={{ justifyContent: 'space-between', marginBottom: 5 }}>
                <span className="secondary">Overall health</span>
                <span className="mono" style={{ color: healthColor(e.health_score) }}>{fmt.num(e.health_score)}/100</span>
              </div>
              <HealthMeter value={e.health_score} />
            </div>
            <div className="small secondary">
              {e.worst_component && e.worst_component.status !== 'good'
                ? <>Weakest: <strong>{e.worst_component.name}</strong> · shortest RUL {fmt.rul(e.min_rul_days)}</>
                : <>All components healthy · shortest RUL {fmt.rul(e.min_rul_days)}</>}
            </div>
            <div className="row small muted" style={{ justifyContent: 'space-between' }}>
              <span>{e.floors} floors · {e.capacity_kg} kg · {e.speed_mps} m/s</span>
              {e.open_complaints > 0 && <span className="badge medium">{e.open_complaints} complaint{e.open_complaints > 1 ? 's' : ''}</span>}
            </div>
          </Link>
        ))}
      </div>
    </>
  )
}

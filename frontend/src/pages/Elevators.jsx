import { Link, useNavigate } from 'react-router-dom'
import { CriticalDot, RiskBadge } from '../components/risk'
import { HealthMeter, Loading, Stat, ErrorBox } from '../components/ui'
import { fmt, healthColor } from '../format'
import { useLiveFetch } from '../live'
import { useCustomLifts } from '../engine/liftStore'
import { COMPONENTS, computeComponent } from '../engine/elevatorConfig'
import { effectiveScenario } from './TractionLiftConfigure'

function CustomLifts() {
  const lifts = useCustomLifts()
  if (!lifts.length) return null
  return (
    <div style={{ margin: '24px 0' }}>
      <div className="page-head">
        <div>
          <h2 style={{ margin: 0 }}>Traction Lift drafts</h2>
          <div className="sub">Built with the New Traction Lift wizard - stored in this browser only, not yet on the backend fleet.</div>
        </div>
      </div>
      <div className="grid g3 elev-grid">
        {lifts.map((lift) => {
          const metrics = COMPONENTS.map((c) => computeComponent(c.type, effectiveScenario(lift, c.type), lift.components[c.type]?.spec))
          const health = Math.round(metrics.reduce((a, m) => a + m.health, 0) / metrics.length)
          return (
            <Link key={lift.id} to={`/lift/${lift.id}`} className="card elev-card">
              <div className="title-row">
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div className="row" style={{ gap: 8 }}><span className="name">{lift.name}</span><span className="lift-type">Traction</span><span className="demo-tag">Draft</span></div>
                  <div className="small muted" style={{ marginTop: 2 }}>{lift.floors} floors · {lift.capacity_kg} kg · {lift.speed_mps} m/s</div>
                </div>
              </div>
              <div className="elev-health-sec">
                <div className="row small" style={{ justifyContent: 'space-between', marginBottom: 5 }}>
                  <span className="secondary">Overall health</span>
                  <span className="mono" style={{ color: healthColor(health) }}>{fmt.num(health)}/100</span>
                </div>
                <HealthMeter value={health} />
              </div>
            </Link>
          )
        })}
      </div>
    </div>
  )
}

export default function Elevators() {
  const navigate = useNavigate()
  const { data, error } = useLiveFetch('/elevators')
  if (error && !data) return <ErrorBox error={error} />
  if (!data) return <Loading />
  // Counts come from the shared risk roll-up the backend attaches to every
  // elevator, so the fleet tiles agree with the elevator pages exactly.
  const count = (level) => data.reduce((a, e) => a + (e.risk?.counts?.[level] || 0), 0)
  const avg = data.reduce((a, e) => a + (e.health_score || 0), 0) / Math.max(1, data.length)
  const complaints = data.reduce((a, e) => a + e.open_complaints, 0)
  return (
    <>
      <div className="page-head fleet-header">
        <div>
          <h1>Elevator fleet</h1>
          <div className="sub">Suspension health across {data.length} elevators - ropes, sheave, bearings and traction machine.</div>
        </div>
        <Link to="/new-lift" className="btn primary">+ New Traction Lift</Link>
      </div>
      <div className="grid g4 stat-grid" style={{ marginBottom: 16 }}>
        <Stat label="Fleet health (avg)" value={fmt.num(avg)} unit="/100" color={healthColor(avg)} />
        <Stat label="Critical - replace now" value={count('critical')} foot="failure likely within 30 days" color={count('critical') ? 'var(--critical)' : undefined} />
        <Stat label="High risk - replace soon" value={count('high')} foot="within this maintenance interval" color={count('high') ? 'var(--serious)' : undefined} />
        <Stat label="Open complaints" value={complaints} foot="from public reports" />
      </div>
      <div className="grid g3 elev-grid">
        {data.map((e) => (
          <Link key={e.id} to={`/elevators/${e.id}`} className={`card elev-card risk-${e.risk?.worst || 'good'}`}>
            <div className="title-row">
              <div style={{ minWidth: 0, flex: 1 }}>
                <div className="row" style={{ gap: 8 }}>
                  <CriticalDot level={e.risk?.worst} />
                  <span className="name">{e.name}</span>
                  <span className="lift-type">Traction</span>
                  {e.is_demo && <span className="demo-tag">Demo</span>}
                </div>
                <div className="small muted" style={{ marginTop: 2 }}>{e.building} · {e.location}</div>
              </div>
              <div style={{ marginLeft: 'auto', flexShrink: 0 }}><RiskBadge level={e.risk?.worst || 'good'} /></div>
            </div>
            <div className="elev-health-sec">
              <div className="row small" style={{ justifyContent: 'space-between', marginBottom: 5 }}>
                <span className="secondary">Overall health</span>
                <span className="mono" style={{ color: healthColor(e.health_score) }}>{fmt.num(e.health_score)}/100</span>
              </div>
              <HealthMeter value={e.health_score} />
            </div>
            <div className="row small elev-material" style={{ justifyContent: 'space-between', alignItems: 'center', marginTop: 6 }}>
              <span className="secondary">
                {e.primary_material ? <>Material: <strong>{e.primary_material}</strong></> : 'No material spec on file'}
              </span>
              {(e.material_completeness_pct ?? 0) < 100 && (
                <span className="badge medium" title="Fill in the material specification"
                  onClick={(ev) => { ev.preventDefault(); ev.stopPropagation(); navigate(`/elevators/${e.id}/materials`) }}>
                  Spec {e.material_completeness_pct ?? 0}% complete
                </span>
              )}
            </div>
            {e.risk?.banner ? (
              <div className={`card-banner risk-${e.risk.worst}`}>{e.risk.banner}</div>
            ) : (
              <div className="card-banner empty-banner" />
            )}
            <div className="small secondary elev-weakest">
              {e.worst_component && e.worst_component.status !== 'good'
                ? <>Weakest: <strong>{e.worst_component.name}</strong> · shortest RUL {fmt.rul(e.min_rul_days)}</>
                : <>All components healthy · shortest RUL {fmt.rul(e.min_rul_days)}</>}
            </div>
            <div className="row small muted elev-foot" style={{ justifyContent: 'space-between', marginTop: 'auto' }}>
              <span>{e.floors} floors · {e.capacity_kg} kg · {e.speed_mps} m/s</span>
              {e.open_complaints > 0 && <span className="badge medium">{e.open_complaints} complaint{e.open_complaints > 1 ? 's' : ''}</span>}
            </div>
          </Link>
        ))}
      </div>
      <CustomLifts />
    </>
  )
}

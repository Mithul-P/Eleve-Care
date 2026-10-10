// Section 4 - Sensor Input entry point. Clicking "Sensor inputs" in the nav
// must always land here first: a plain list of every lift, independent of
// whichever elevator page was last open elsewhere in the app. Only picking a
// lift from this list moves on to its sensors (/elevators/:id/sensors).
import { Link } from 'react-router-dom'
import BackBar from '../components/BackBar'
import { RiskBadge } from '../components/risk'
import { ErrorBox, Loading } from '../components/ui'
import { fmt } from '../format'
import { useLiveFetch } from '../live'

export default function SensorInputList() {
  const { data, error } = useLiveFetch('/elevators')
  const trail = [{ label: 'Elevators', to: '/elevators' }, { label: 'Sensor inputs' }]
  if (error && !data) return (<><BackBar trail={trail} /><ErrorBox error={error} /></>)
  if (!data) return (<><BackBar trail={trail} /><Loading /></>)
  return (
    <>
      <BackBar trail={trail} />
      <div className="page-head">
        <div>
          <h1>Sensor inputs</h1>
          <div className="sub">Choose a lift to see its sensor registry, live stream and ingestion tools.</div>
        </div>
      </div>
      <div className="grid g3 elev-grid">
        {data.map((e) => (
          <Link key={e.id} to={`/elevators/${e.id}/sensors`} className={`card elev-card risk-${e.risk?.worst || 'good'}`}>
            <div className="title-row">
              <div style={{ minWidth: 0, flex: 1 }}>
                <div className="row" style={{ gap: 8 }}>
                  <span className="name">{e.name}</span>
                  <span className="lift-type">Traction</span>
                  {e.is_demo && <span className="demo-tag">Demo</span>}
                </div>
                <div className="small muted" style={{ marginTop: 2 }}>{e.building} · {e.location}</div>
              </div>
              <div style={{ marginLeft: 'auto', flexShrink: 0 }}><RiskBadge level={e.risk?.worst || 'good'} /></div>
            </div>
            <div className="row small muted" style={{ justifyContent: 'space-between', marginTop: 10 }}>
              <span>Overall health {fmt.num(e.health_score)}/100</span>
              <span>{e.floors} floors</span>
            </div>
          </Link>
        ))}
      </div>
      {data.length === 0 && <div className="empty">No elevators yet.</div>}
    </>
  )
}

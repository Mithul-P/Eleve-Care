import { useParams } from 'react-router-dom'
import BackBar from '../components/BackBar'
import { HealthMeter, Loading } from '../components/ui'
import { COMPONENTS, EXTRA_FIELDS, completeness, computeComponent, formatRul, resolveInputs } from '../engine/elevatorConfig'
import { useCustomLift } from '../engine/liftStore'
import { effectiveScenario } from './TractionLiftConfigure'

export default function TractionLiftSummary() {
  const { id } = useParams()
  const [lift] = useCustomLift(id)
  if (!lift) return (<><BackBar trail={[{ label: 'Elevators', to: '/elevators' }, { label: 'Lift' }, { label: 'Summary' }]} /><Loading what="Loading summary" /></>)

  return (
    <>
      <BackBar trail={[{ label: 'Elevators', to: '/elevators' }, { label: lift.name, to: `/lift/${id}` }, { label: 'Elevator summary' }]} />
      <div className="page-head">
        <div>
          <h1>Elevator summary</h1>
          <div className="sub">{lift.name} · which inputs are your own spec vs. still running on documented defaults, per component.</div>
        </div>
      </div>

      <div className="stack">
        {COMPONENTS.map((def) => {
          const comp = lift.components[def.type]
          const scenario = effectiveScenario(lift, def.type)
          const metrics = computeComponent(def.type, scenario, comp.spec)
          const pct = completeness(def.type, comp.spec)
          const { live, decorative } = resolveInputs(def.type, comp.spec)
          return (
            <div className="card" key={def.type}>
              <div className="card-head">
                <h3>{def.name}</h3>
                <span className="hint">{def.model === 'fatigue' ? 'Fatigue model' : 'Wear model'} · scenario {scenario}</span>
                <div className="right small muted">Health {metrics.health}% · P30d {metrics.p30d}% · RUL {formatRul(metrics.rul_days)}</div>
              </div>
              <div className="row" style={{ marginBottom: 10 }}>
                <strong>Completeness {pct}%</strong>
                <div style={{ width: 160 }}><HealthMeter value={pct} /></div>
              </div>
              {live.length > 0 && (
                <div className="small" style={{ marginBottom: 10 }}>
                  <div className="muted" style={{ marginBottom: 4 }}>Inputs used by the health/P30d/RUL calculation:</div>
                  <table><thead><tr><th>Input</th><th>Value</th><th>Source</th></tr></thead>
                    <tbody>{live.map((i) => (
                      <tr key={i.key}><td>{i.label}</td><td className="mono">{i.value} {i.unit}</td>
                        <td><span className={`badge sm ${i.source === 'user' ? 'good' : 'neutral'}`}>{i.source === 'user' ? 'from your spec' : 'default'}</span></td></tr>
                    ))}</tbody>
                  </table>
                </div>
              )}
              {decorative.length > 0 && (
                <div className="small muted">
                  Collected for provenance, not yet feeding the calculation: {decorative.map((i) => i.label).join(', ')}.
                </div>
              )}
              {EXTRA_FIELDS[def.type]?.every((f) => !f.live) && (
                <div className="small muted">No dedicated degradation law for this component yet - every field above is decorative (stored, not live).</div>
              )}
            </div>
          )
        })}
      </div>
    </>
  )
}

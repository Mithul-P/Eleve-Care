import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import BackBar from '../components/BackBar'
import { HealthMeter, Loading, Seg } from '../components/ui'
import {
  COMPONENTS, PRESETS, EXTRA_FIELDS, applyPreset, autoScenarioFor,
  completeness, computeComponent, formatRul,
} from '../engine/elevatorConfig'
import { useCustomLift } from '../engine/liftStore'
import TractionMaterialForm from './TractionMaterialForm'

const SCENARIO_SEG = [
  { value: 'good', label: 'Good' }, { value: 'medium', label: 'Medium' }, { value: 'risk', label: 'Risk' }, { value: 'auto', label: 'Auto (mixed)' },
]
const OVERRIDE_SEG = [
  { value: 'inherit', label: 'Inherit' }, { value: 'good', label: 'Good' }, { value: 'medium', label: 'Medium' }, { value: 'risk', label: 'Risk' },
]

export function effectiveScenario(lift, type) {
  const override = lift.components[type]?.scenario || 'inherit'
  if (override !== 'inherit') return override
  return lift.scenario === 'auto' ? autoScenarioFor(lift.id, type) : lift.scenario
}

const STATUS_PILL = { good: 'GOOD', medium: 'MEDIUM', critical: 'CRITICAL' }

function ComponentCard({ lift, def, update }) {
  const [open, setOpen] = useState(false)
  const comp = lift.components[def.type]
  const scenario = effectiveScenario(lift, def.type)
  const metrics = computeComponent(def.type, scenario, comp.spec)
  const pct = completeness(def.type, comp.spec)
  const touched = !!comp.touched

  const setSpec = (spec) => update((prev) => ({
    ...prev,
    components: { ...prev.components, [def.type]: { ...prev.components[def.type], spec, touched: true } },
  }))
  const setOverride = (v) => update((prev) => ({
    ...prev,
    components: { ...prev.components, [def.type]: { ...prev.components[def.type], scenario: v } },
  }))

  return (
    <div className={`card lift-comp-card risk-${metrics.status === 'critical' ? 'critical' : metrics.status}`}>
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <strong>{def.name}</strong>
        <span className={`badge ${metrics.status === 'good' ? 'good' : metrics.status === 'medium' ? 'medium' : 'at-risk'}`}>{STATUS_PILL[metrics.status]}</span>
      </div>
      <div className="small muted" style={{ margin: '4px 0 10px' }}>{def.model === 'fatigue' ? 'Fatigue model' : 'Wear model'} · Completeness {pct}%{touched ? '' : ' (defaults)'}</div>

      <div className="row" style={{ gap: 16, marginBottom: 10 }}>
        <div><div className="small muted">Health</div><strong>{metrics.health}%</strong></div>
        <div><div className="small muted">P30d</div><strong>{metrics.p30d}%</strong></div>
        <div><div className="small muted">RUL</div><strong>{formatRul(metrics.rul_days)}</strong></div>
      </div>
      <HealthMeter value={metrics.health} />

      <div className="small muted" style={{ marginTop: 10, marginBottom: 4 }}>
        Scenario: <strong>{comp.scenario === 'inherit' ? `${scenario} (inherited)` : scenario}</strong>
      </div>
      <Seg value={comp.scenario} onChange={setOverride} options={OVERRIDE_SEG} label={`${def.name} scenario`} />

      <div style={{ marginTop: 10 }}>
        <button type="button" className="btn sm" onClick={() => setOpen((o) => !o)}>
          {open ? 'Hide material form' : 'Select material'}
        </button>
      </div>
      {open && (
        <div style={{ marginTop: 10 }}>
          <TractionMaterialForm type={def.type} extraFields={EXTRA_FIELDS[def.type]} spec={comp.spec} onChange={setSpec} />
        </div>
      )}
    </div>
  )
}

export default function TractionLiftConfigure() {
  const { id } = useParams()
  const [lift, update] = useCustomLift(id)
  if (!lift) return (<><BackBar trail={[{ label: 'Elevators', to: '/elevators' }, { label: 'Lift' }]} /><Loading what="Loading lift" /></>)

  const selected = COMPONENTS.filter((c) => lift.components[c.type]?.touched).length
  const allMetrics = COMPONENTS.map((c) => computeComponent(c.type, effectiveScenario(lift, c.type), lift.components[c.type]?.spec))
  const overallHealth = Math.round(allMetrics.reduce((a, m) => a + m.health, 0) / allMetrics.length)
  const needAttention = allMetrics.filter((m) => m.status !== 'good').length

  const useDefaults = () => update((prev) => {
    const components = { ...prev.components }
    COMPONENTS.forEach((c) => {
      if (components[c.type]?.touched) return
      const first = PRESETS[c.type]?.[0]
      const spec = first ? applyPreset(c.type, first.id) : {}
      components[c.type] = { ...components[c.type], spec, touched: true }
    })
    return { ...prev, components }
  })
  const setLiftScenario = (v) => update((prev) => ({ ...prev, scenario: v }))

  return (
    <>
      <BackBar trail={[{ label: 'Elevators', to: '/elevators' }, { label: lift.name }]} />
      <div className="page-head">
        <div>
          <h1>{lift.name} <span className="lift-type">Traction</span></h1>
          <div className="sub">{lift.floors} floors · {lift.capacity_kg} kg · {lift.speed_mps} m/s · {lift.trips_per_day} trips/day · installed {lift.install_date}</div>
        </div>
        <div className="right" style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <Seg value={lift.scenario} onChange={setLiftScenario} options={SCENARIO_SEG} label="Lift scenario" />
          <Link className="btn ghost" to={`/lift/${id}/summary`}>Elevator summary →</Link>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="row" style={{ justifyContent: 'space-between', marginBottom: 8 }}>
          <strong>Materials selected: {selected} / 9</strong>
          <button type="button" className="btn sm" onClick={useDefaults}>Use default materials</button>
          <span className="small muted" style={{ marginLeft: 'auto' }}>Overall health {overallHealth} · {needAttention} need attention</span>
        </div>
        <HealthMeter value={(selected / 9) * 100} />
      </div>

      <div className="grid g3" id="components">
        {COMPONENTS.map((def) => <ComponentCard key={def.type} lift={lift} def={def} update={update} />)}
      </div>

      <div className="small muted" style={{ marginTop: 16 }}>
        Prototype / decision-support only - not a certified safety system. Every parameter in this wizard is editable in{' '}
        <code className="mono">src/engine/elevatorConfig.js</code>.
      </div>
    </>
  )
}

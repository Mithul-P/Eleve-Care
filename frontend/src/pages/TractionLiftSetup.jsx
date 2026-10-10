import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import BackBar from '../components/BackBar'
import { Seg } from '../components/ui'
import { LIFT_DEFAULTS } from '../engine/elevatorConfig'
import { createLift } from '../engine/liftStore'

const SCENARIO_OPTIONS = [
  { value: 'good', label: 'Good' },
  { value: 'medium', label: 'Medium' },
  { value: 'risk', label: 'Risk' },
  { value: 'auto', label: 'Auto (mixed)' },
]

function NumField({ label, value, onChange, step, min }) {
  return (
    <div className="field">
      <label>{label}</label>
      <input type="number" value={value} step={step} min={min} onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))} />
    </div>
  )
}

export default function TractionLiftSetup() {
  const navigate = useNavigate()
  const [f, setF] = useState({ ...LIFT_DEFAULTS, name: 'New Traction Lift' })
  const set = (k, v) => setF((x) => ({ ...x, [k]: v }))

  const submit = (e) => {
    e.preventDefault()
    const lift = createLift({
      name: f.name || 'New Traction Lift',
      type: 'traction',
      floors: Number(f.floors) || 1,
      capacity_kg: Number(f.capacity_kg) || 0,
      speed_mps: Number(f.speed_mps) || 0,
      install_date: f.install_date,
      trips_per_day: Number(f.trips_per_day) || 0,
      rope_count: Number(f.rope_count) || 1,
      rope_diameter_mm: Number(f.rope_diameter_mm) || 1,
      sheave_diameter_mm: Number(f.sheave_diameter_mm) || 1,
      scenario: f.scenario,
    })
    navigate(`/lift/${lift.id}`)
  }

  return (
    <>
      <BackBar trail={[{ label: 'Elevators', to: '/elevators' }, { label: 'Set up a Traction Lift' }]} />
      <div className="page-head">
        <div>
          <h1>Set up a Traction Lift</h1>
          <div className="sub">Every field is pre-filled with a sensible default - submit as-is, or adjust anything below.</div>
        </div>
      </div>
      <form className="card" style={{ maxWidth: 780 }} onSubmit={submit}>
        <div className="card-head"><h3>Common</h3></div>
        <div className="grid g3" style={{ marginBottom: 8 }}>
          <div className="field span2"><label>Elevator name / ID</label><input value={f.name} onChange={(e) => set('name', e.target.value)} required /></div>
          <NumField label="Floors" value={f.floors} onChange={(v) => set('floors', v)} min={2} />
          <NumField label="Rated load (kg)" value={f.capacity_kg} onChange={(v) => set('capacity_kg', v)} step={50} min={100} />
          <NumField label="Rated speed (m/s)" value={f.speed_mps} onChange={(v) => set('speed_mps', v)} step={0.1} min={0.1} />
          <div className="field"><label>Installation date</label><input type="date" value={f.install_date} onChange={(e) => set('install_date', e.target.value)} /></div>
          <NumField label="Average trips per day" value={f.trips_per_day} onChange={(v) => set('trips_per_day', v)} min={0} />
        </div>

        <div className="card-head"><h3>Traction Lift Extra</h3></div>
        <div className="grid g3" style={{ marginBottom: 8 }}>
          <NumField label="Number of ropes/belts" value={f.rope_count} onChange={(v) => set('rope_count', v)} min={2} />
          <NumField label="Rope diameter (mm)" value={f.rope_diameter_mm} onChange={(v) => set('rope_diameter_mm', v)} step={0.5} min={1} />
          <NumField label="Sheave diameter (mm)" value={f.sheave_diameter_mm} onChange={(v) => set('sheave_diameter_mm', v)} step={10} min={50} />
        </div>

        <div className="card-head"><h3>Scenario</h3></div>
        <div style={{ marginBottom: 8 }}>
          <Seg value={f.scenario} onChange={(v) => set('scenario', v)} options={SCENARIO_OPTIONS} label="Scenario" />
          <div className="small muted" style={{ marginTop: 6 }}>
            {f.scenario === 'auto'
              ? 'Auto seeds a mix of about 60% Good, 25% Medium and 15% Risk across the 9 components, so the demo lift shows a realistic spread from the start.'
              : `Every component starts on "${f.scenario}" until you override it individually on the next screen.`}
          </div>
        </div>

        <div className="row" style={{ marginTop: 12, justifyContent: 'flex-end' }}>
          <button type="button" className="btn ghost" onClick={() => navigate('/elevators')}>Cancel</button>
          <button type="submit" className="btn primary">Create and choose materials →</button>
        </div>
      </form>
    </>
  )
}

// Section 2 - MATERIALS & SPECIFICATION. One form per component (the traction
// machine has repeatable rows, one per part). Presets auto-fill; every field
// shows its unit; blanks are tagged "using default value"; GSM is forced to
// "Not applicable" for steel wire / cast / forged parts. Saving triggers a
// fresh prediction on the backend.
import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import BackBar from '../components/BackBar'
import { HealthMeter, Loading } from '../components/ui'
import { useLive, useLiveFetch } from '../live'

const GSM_RE = /belt|liner|lining|polyurethane|PU/i
const gsmApplies = (type, materialType) => type !== 'bearing' && GSM_RE.test(materialType || '')

function Field({ f, value, onChange, disabled, blank, options }) {
  const unit = f.unit ? <span className="unit-tag">{f.unit}</span> : null
  const tag = blank && !disabled ? <span className="default-tag">using default value</span> : null
  return (
    <div className="field spec-field">
      <label>{f.label} {unit} {tag}</label>
      {options ? (
        <select value={value ?? ''} onChange={(e) => onChange(e.target.value)} disabled={disabled}>
          <option value="">—</option>{options.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      ) : (
        <input value={value ?? ''} onChange={(e) => onChange(e.target.value)} disabled={disabled} placeholder={disabled ? 'Not applicable' : ''} />
      )}
    </div>
  )
}

/** One material row (all eight fields + component extras). */
function SpecFields({ type, schema, row, onChange, extras = true }) {
  const gsmOk = gsmApplies(type, row.material_type)
  const presetId = row.preset || ''
  return (
    <>
      <div className="field">
        <label>Industry preset <span className="small muted">(auto-fills grade, composition, strength and coating - edit anything)</span></label>
        <select value={presetId} onChange={(e) => onChange({ ...row, ...presetFill(schema, e.target.value, type) })}>
          <option value="">Choose a common material…</option>
          {schema.presets.map((p) => <option key={p.id} value={p.id}>{p.part ? `${p.part} · ` : ''}{p.material_type}{p.material_grade ? ` (${p.material_grade})` : ''}</option>)}
          <option value="other">Other (free text)</option>
        </select>
      </div>
      <div className="grid g4 spec-grid">
        {schema.fields.map((f) => {
          const isGsm = f.key === 'gsm'
          const disabled = isGsm && !gsmOk
          return <Field key={f.key} f={f} value={disabled ? 'Not applicable' : row[f.key]} disabled={disabled} blank={!disabled && (row[f.key] == null || row[f.key] === '')} onChange={(v) => onChange({ ...row, [f.key]: v })} />
        })}
        {extras && schema.extra_fields.map((f) => (
          <Field key={f.key} f={f} value={row[f.key]} options={f.options} blank={row[f.key] == null || row[f.key] === ''} onChange={(v) => onChange({ ...row, [f.key]: v })} />
        ))}
      </div>
    </>
  )
}

function presetFill(schema, id, type) {
  if (!id || id === 'other') return { preset: id }
  const p = schema.presets.find((x) => x.id === id)
  if (!p) return {}
  const { id: _id, ...vals } = p
  return { ...vals, preset: id, gsm: p.gsm ?? (gsmApplies(type, p.material_type) ? '' : 'Not applicable') }
}

/** Editable spec for one component. `data` = GET /components/:id/material-spec */
export function MaterialSpecForm({ componentId, data, onSaved, driveType }) {
  const { pushToast } = useLive()
  const [spec, setSpec] = useState(data.spec || {})
  const [busy, setBusy] = useState(false)
  useEffect(() => { setSpec(data.spec || {}) }, [data.updated_at]) // eslint-disable-line react-hooks/exhaustive-deps
  const isMachine = data.type === 'traction'
  const schema = useMemo(() => {
    if (!isMachine) return data.schema
    // drive type only changes the default part rows offered
    const presets = data.schema.presets.filter((p) => !p.drive || (driveType === 'Geared' ? p.drive === 'geared' : p.drive === 'gearless'))
    return { ...data.schema, presets }
  }, [data.schema, driveType, isMachine])
  const rows = spec.rows || []
  const completeness = useMemo(() => {
    const keys = isMachine ? schema.fields.map((f) => f.key) : [...schema.fields, ...schema.extra_fields].map((f) => f.key)
    const src = isMachine ? rows : [spec]
    if (!src.length) return 0
    let filled = 0, total = 0
    src.forEach((r) => keys.forEach((k) => { total += 1; if (r[k] != null && r[k] !== '') filled += 1 }))
    return Math.round((100 * filled) / Math.max(1, total))
  }, [spec, rows, schema, isMachine])

  const save = async () => {
    setBusy(true)
    try {
      const r = await api(`/components/${componentId}/material-spec`, { method: 'PUT', body: { spec } })
      pushToast({ level: 'info', title: 'Specification saved', text: r.repredicted ? `Fresh prediction: health ${Math.round(r.prediction.health_score)}, status ${r.prediction.status}.` : 'Saved. Prediction will refresh with the next reading.' })
      onSaved?.(r)
    } catch (e) { pushToast({ level: 'warning', title: 'Could not save', text: e.message }) }
    finally { setBusy(false) }
  }
  const defaultRows = () => setSpec({ ...spec, rows: schema.presets.map((p) => ({ part: p.part, ...presetFill(schema, p.id, 'traction') })) })

  return (
    <div className="spec-form">
      <div className="row" style={{ marginBottom: 10 }}>
        <strong>Completeness {completeness}%</strong>
        <div style={{ width: 160 }}><HealthMeter value={completeness} /></div>
        {data.updated_at && <span className="small muted">saved {new Date(data.updated_at).toLocaleString('en-IN')}</span>}
        <span className="small muted">{schema.gsm_rule}</span>
      </div>
      {isMachine ? (
        <>
          <div className="row" style={{ marginBottom: 8 }}>
            <span className="small muted">One row per part ({driveType === 'Geared' ? 'geared drive: worm gear row available' : 'gearless PM: magnet row available'}).</span>
            <button className="btn sm" type="button" onClick={defaultRows}>Fill typical parts</button>
            <button className="btn sm" type="button" onClick={() => setSpec({ ...spec, rows: [...rows, {}] })}>+ Add part row</button>
          </div>
          {rows.length === 0 && <div className="empty">No parts yet - use "Fill typical parts" or add a row.</div>}
          {rows.map((row, i) => (
            <div key={i} className="card spec-row">
              <div className="row" style={{ marginBottom: 6 }}>
                <input style={{ width: 240 }} placeholder="Part (e.g. Windings)" value={row.part || ''} onChange={(e) => setSpec({ ...spec, rows: rows.map((r, j) => (j === i ? { ...r, part: e.target.value } : r)) })} />
                <button className="btn ghost sm" type="button" onClick={() => setSpec({ ...spec, rows: rows.filter((_, j) => j !== i) })}>Remove</button>
              </div>
              <SpecFields type="traction" schema={schema} row={row} onChange={(r) => setSpec({ ...spec, rows: rows.map((x, j) => (j === i ? r : x)) })} extras={false} />
              <Field f={schema.extra_fields[0]} value={row.insulation_class} options={schema.extra_fields[0].options} blank={!row.insulation_class} onChange={(v) => setSpec({ ...spec, rows: rows.map((x, j) => (j === i ? { ...x, insulation_class: v } : x)) })} />
            </div>
          ))}
        </>
      ) : (
        <SpecFields type={data.type} schema={schema} row={spec} onChange={setSpec} />
      )}
      <div className="row" style={{ marginTop: 8 }}>
        <button className="btn primary" onClick={save} disabled={busy}>{busy ? 'Saving…' : 'Save specification & re-predict'}</button>
        <button className="btn ghost" type="button" onClick={() => setSpec(data.spec || {})}>Reset</button>
      </div>
      <PhysicsInputs inputs={data.physics_inputs} />
    </div>
  )
}

/** Which physics inputs come from the spec and which are defaults. */
export function PhysicsInputs({ inputs }) {
  if (!inputs?.length) return null
  return (
    <div className="small" style={{ marginTop: 12 }}>
      <div className="muted" style={{ marginBottom: 4 }}>Physics inputs used by the degradation law:</div>
      <table><thead><tr><th>Input</th><th>Value</th><th>Source</th></tr></thead>
        <tbody>{inputs.map((i) => (
          <tr key={i.key}><td>{i.label}</td><td className="mono">{typeof i.value === 'number' ? (i.value > 1e5 ? i.value.toExponential(3) : i.value) : i.value} {i.unit}</td>
            <td><span className={`badge sm ${i.source === 'user' ? 'good' : 'neutral'}`}>{i.source === 'user' ? 'from your spec' : 'default'}</span></td></tr>))}</tbody></table>
    </div>
  )
}

/** First-time setup page: all components of one elevator. */
export default function MaterialSpec() {
  const { id } = useParams()
  const [reload, setReload] = useState(0)
  const { data, error } = useLiveFetch(`/elevators/${id}/material-spec`, [reload])
  const { data: e } = useLiveFetch(`/elevators/${id}`)
  const { pushToast } = useLive()
  if (error) return <div className="error">{error.message}</div>
  if (!data || !e) return <Loading what="Loading specification" />
  const setDrive = async (drive_type) => { await api(`/elevators/${id}`, { method: 'PATCH', body: { drive_type } }); pushToast({ level: 'info', title: 'Drive type saved', text: drive_type }); setReload((r) => r + 1) }
  return (
    <div>
      <BackBar trail={[{ label: 'Elevators', to: '/elevators' }, { label: e.name, to: `/elevators/${id}` }, { label: 'Materials & specification' }]} />
      <div className="page-head">
        <div><h1>Materials &amp; specification</h1><div className="sub">{e.name} · <span className="lift-type">Traction lift</span> · fill once per elevator; every value feeds the physics laws (blank = default).</div></div>
        <div className="actions">
          <label style={{ margin: 0 }}>Drive type <select value={data.drive_type} onChange={(ev) => setDrive(ev.target.value)} style={{ width: 160, display: 'inline-block', marginLeft: 6 }}><option>Gearless PM</option><option>Geared</option></select></label>
          <span className="badge neutral">Overall {data.overall_completeness_pct}% complete</span>
        </div>
      </div>
      <div className="stack">
        {data.components.map((c) => (
          <div className="card" key={c.component_id}>
            <div className="card-head"><h3>{c.name}</h3><span className="hint">{c.type}</span><div className="right"><Link className="btn ghost sm" to={`/components/${c.component_id}`}>Open component</Link></div></div>
            <MaterialSpecForm componentId={c.component_id} data={c} driveType={data.drive_type} onSaved={() => setReload((r) => r + 1)} />
          </div>
        ))}
      </div>
    </div>
  )
}

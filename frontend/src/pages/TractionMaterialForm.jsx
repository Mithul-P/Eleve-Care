import { FIELDS, PRESETS, applyPreset, completeness, gsmApplicable } from '../engine/elevatorConfig'

function Field({ f, value, onChange, disabled, blank, options, decorative }) {
  const unit = f.unit ? <span className="unit-tag">{f.unit}</span> : null
  const tag = blank && !disabled ? <span className="default-tag">using default value</span> : null
  const deco = decorative ? <span className="default-tag" title="Collected for provenance, not yet used in the health/P30d/RUL calculation">not yet used in prediction</span> : null
  return (
    <div className="field spec-field">
      <label>{f.label} {unit} {tag} {deco}</label>
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

/**
 * The same form shape for every one of the 9 components: preset picker + the
 * 8 common fields + that component's extra fields. A blank field keeps
 * showing a valid Health/P30d/RUL upstream (computeComponent falls back to
 * the field's documented default), it just gets an amber "using default
 * value" tag here so the gap is visible.
 */
export default function TractionMaterialForm({ type, extraFields, spec, onChange }) {
  const row = spec || {}
  const gsmOk = gsmApplicable(type, row.material_type)
  const pct = completeness(type, row)
  return (
    <div className="spec-form">
      <div className="row" style={{ marginBottom: 10 }}>
        <strong>Completeness {pct}%</strong>
        <span className="small muted">GSM applies to belts/liners/PU-type materials only; auto-set to "Not applicable" otherwise.</span>
      </div>
      <div className="field">
        <label>Industry preset <span className="small muted">(auto-fills the fields below - edit anything after)</span></label>
        <select value={row.preset || ''} onChange={(e) => {
          if (!e.target.value) return onChange({ ...row, preset: '' })
          const filled = applyPreset(type, e.target.value)
          onChange(filled ? { ...row, ...filled } : { ...row, preset: 'other' })
        }}>
          <option value="">Choose a common material…</option>
          {(PRESETS[type] || []).map((p) => <option key={p.id} value={p.id}>{p.material_type}{p.material_grade ? ` (${p.material_grade})` : ''}</option>)}
          <option value="other">Other (free text)</option>
        </select>
      </div>
      <div className="grid g4 spec-grid">
        {FIELDS.map((f) => {
          const isGsm = f.key === 'gsm'
          const disabled = isGsm && !gsmOk
          const value = disabled ? 'Not applicable' : row[f.key]
          return <Field key={f.key} f={f} value={value} disabled={disabled}
            blank={!disabled && (row[f.key] == null || row[f.key] === '')}
            onChange={(v) => onChange({ ...row, [f.key]: v })} />
        })}
        {extraFields.map((f) => (
          <Field key={f.key} f={f} value={row[f.key]} options={f.options} decorative={!f.live}
            blank={row[f.key] == null || row[f.key] === ''}
            onChange={(v) => onChange({ ...row, [f.key]: v })} />
        ))}
      </div>
    </div>
  )
}

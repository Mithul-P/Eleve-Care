import { Fragment, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ReferenceArea, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { api } from '../api'
import BackBar, { useStickyParam } from '../components/BackBar'
import { CriticalDot, RecommendationCard, ReplaceTag, RiskBadge } from '../components/risk'
import { ChartTooltip, ErrorBox, HealthRing, Loading, Seg, Stat, StatusBadge, axisProps } from '../components/ui'
import { fmt, healthColor } from '../format'
import { IconWrench } from '../icons'
import { useLive, useLiveFetch } from '../live'
import { MaterialSpecForm } from './MaterialSpec'
import { THRESHOLD, classify } from '../risk'

const INDICATORS = {
  rope: ['tension_imbalance_pct', 'vib_rms', 'fault_band_ratio', 'tension_deviation_pct'],
  sheave: ['vib_rms', 'fault_band_ratio', 'motor_current_ratio'],
  bearing: ['vib_kurtosis', 'temperature_rise', 'fault_band_ratio', 'vib_rms', 'vib_crest'],
  traction: ['motor_current_ratio', 'temperature_rise', 'fault_band_ratio', 'vib_rms'],
}
const FAULT_BAND = { rope: [4, 16], sheave: [20, 60], bearing: [250, 400], traction: [90, 110] }

const tickDate = (t) => fmt.dateShort(t)

function FactorList({ factors }) {
  if (!factors?.length) return <div className="empty">All monitored indicators are within healthy limits.</div>
  return factors.map((f) => {
    const sev = Math.min(1.5, f.severity)
    const pct = (sev / 1.5) * 100
    const color = f.severity >= 1 ? 'var(--critical)' : f.severity >= 0.5 ? 'var(--warning)' : 'var(--good)'
    return (
      <div className="factor" key={f.feature}>
        <div className="factor-top">
          <strong>{f.label}</strong>
          <span className="small muted" title="Physics = engineering weight in the severity score; ML = permutation importance in the failure model">physics {Math.round((f.physics_weight ?? 0) * 100)}% · ML {Math.round(f.model_importance * 100)}%</span>
          <span className="val">{f.display_value}</span>
        </div>
        <div className="sev-track" title={`healthy ${f.healthy} · alarm ${f.alarm}`}>
          <div className="sev-fill" style={{ width: `${pct}%`, background: color }} />
          <div className="sev-alarm" style={{ left: `${(1 / 1.5) * 100}%` }} title="alarm limit" />
        </div>
        <div className="factor-advice">{f.advice}</div>
      </div>
    )
  })
}

function ReplacementModal({ component, onCancel, onConfirm, busy }) {
  const { data: spec } = useLiveFetch(`/components/${component.id}/material-spec`)
  const [presetId, setPresetId] = useState('__keep__')
  const presets = spec?.schema?.presets || []
  const repeatable = spec?.schema?.repeatable
  return (
    <div className="modal-backdrop" onClick={onCancel} style={{
      position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
      background: 'rgba(0,0,0,0.65)', zIndex: 1000,
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20
    }}>
      <div className="card modal-content" onClick={(e) => e.stopPropagation()} style={{
        maxWidth: 480, width: '100%',
        border: '1px solid var(--border-strong)', boxShadow: '0 12px 32px rgba(0,0,0,0.5)'
      }}>
        <div className="card-head"><h3 style={{ margin: 0 }}>Record replacement</h3></div>
        <p className="secondary" style={{ marginTop: 4 }}>Replacing "{component.name}" resets its wear state.</p>
        {!spec ? <Loading what="Loading material options" /> : repeatable ? (
          <p className="small muted">
            This component has several parts (rows) - after confirming, update each part's material
            in the Materials &amp; specification section below.
          </p>
        ) : presets.length > 0 ? (
          <div style={{ marginBottom: 12 }}>
            <label className="small muted" style={{ display: 'block', marginBottom: 4 }}>Material used for the new part</label>
            <select value={presetId} onChange={(e) => setPresetId(e.target.value)} style={{ width: '100%' }}>
              <option value="__keep__">Keep current material spec</option>
              {presets.map((p) => (
                <option key={p.id} value={p.id}>{p.material_type}{p.material_grade ? ` (${p.material_grade})` : ''}</option>
              ))}
            </select>
          </div>
        ) : (
          <p className="small muted">No material presets defined for this component type - the current spec is kept.</p>
        )}
        <div className="row" style={{ justifyContent: 'flex-end', gap: 8, marginTop: 8 }}>
          <button className="btn sm ghost" onClick={onCancel} disabled={busy}>Cancel</button>
          <button className="btn sm primary" disabled={busy || !spec}
            onClick={() => onConfirm(presetId === '__keep__' ? null : presetId)}>
            {busy ? 'Recording…' : 'Confirm replacement'}
          </button>
        </div>
      </div>
    </div>
  )
}

function Maintenance({ component, onDone }) {
  const [busy, setBusy] = useState(false)
  const [showReplace, setShowReplace] = useState(false)
  const { pushToast } = useLive()
  const record = async (kind, materialPresetId) => {
    setBusy(true)
    try {
      const body = { kind, notes: '' }
      if (kind === 'replacement' && materialPresetId) body.material_preset_id = materialPresetId
      await api(`/components/${component.id}/maintenance`, { method: 'POST', body })
      pushToast({ level: 'info', title: kind === 'replacement' ? 'Replacement recorded' : 'Inspection recorded', text: component.name })
      onDone()
    } catch (e) { pushToast({ level: 'warning', title: 'Could not record maintenance', text: e.message }) }
    finally { setBusy(false); setShowReplace(false) }
  }
  return (
    <div className="card">
      <div className="card-head"><IconWrench style={{ width: 16 }} /><h3>Maintenance</h3></div>
      <dl className="kv" style={{ marginBottom: 12 }}>
        <dt>Installed</dt><dd>{fmt.date(component.install_date)}</dd>
        <dt>Last inspection</dt><dd>{fmt.date(component.last_inspection)}</dd>
        <dt>Next checkup</dt><dd style={{ color: component.checkup_overdue ? 'var(--warning)' : undefined }}>{fmt.date(component.next_checkup_due)}{component.checkup_overdue ? ' (overdue)' : ''}</dd>
        <dt>Fatigue life used</dt><dd>{fmt.pct((component.fatigue_index || 0) * 100)}</dd>
        {component.material?.label && <><dt>Material</dt><dd>{component.material.label}</dd></>}
      </dl>
      <div className="row">
        <button className="btn sm" disabled={busy} onClick={() => record('inspection')}>Record inspection</button>
        <button className="btn sm" disabled={busy} onClick={() => setShowReplace(true)}>Record replacement</button>
      </div>
      {component.maintenance_log?.length > 0 && (
        <table style={{ marginTop: 12 }}>
          <tbody>
            {component.maintenance_log.map((m) => (
              <tr key={m.id}><td className="small">{fmt.date(m.timestamp)}</td><td className="small">{m.kind}</td><td className="small muted">{m.by}</td></tr>
            ))}
          </tbody>
        </table>
      )}
      {showReplace && (
        <ReplacementModal component={component} busy={busy}
          onCancel={() => setShowReplace(false)}
          onConfirm={(presetId) => record('replacement', presetId)} />
      )}
    </div>
  )
}

// Client-side single-sided amplitude spectrum (Hann window) - used only for live frames,
// where the server has not analysed the trace yet. 256 samples: cheap enough.
function dftAmplitude(x) {
  const n = x.length
  const mean = x.reduce((a, b) => a + b, 0) / n
  const han = (i) => 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (n - 1))
  let wsum = 0
  for (let i = 0; i < n; i++) wsum += han(i)
  const out = []
  for (let k = 1; k <= n / 2; k++) {
    let re = 0, im = 0
    for (let i = 0; i < n; i++) {
      const v = (x[i] - mean) * han(i)
      re += v * Math.cos((2 * Math.PI * k * i) / n)
      im -= v * Math.sin((2 * Math.PI * k * i) / n)
    }
    out.push({ f: (k * 1000) / n, a: (2 * Math.sqrt(re * re + im * im)) / wsum })
  }
  return out
}

const LEVEL_COLOR = { normal: 'var(--good)', watch: 'var(--warning)', alarm: 'var(--critical)' }

function SpectrumChart({ data, band, lines, unit = 'g', height = 170 }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="var(--grid)" vertical={false} />
        <XAxis dataKey="f" type="number" domain={[0, 500]} {...axisProps} tickFormatter={(f) => `${f}`} unit=" Hz" />
        <YAxis {...axisProps} width={50} tickFormatter={(v) => v.toFixed(3)} />
        {band && <ReferenceArea x1={band[0]} x2={band[1]} fill="var(--series-2)" fillOpacity={0.12} stroke="none" />}
        {lines?.map((l) => (
          <ReferenceLine key={l.name} x={l.hz} stroke={l.elevated ? 'var(--critical)' : l.detected ? 'var(--warning)' : 'var(--text-muted)'}
            strokeDasharray="3 3" label={{ value: l.short, position: 'top', fill: 'var(--text-muted)', fontSize: 10 }} />
        ))}
        <Tooltip content={<ChartTooltip labelFormatter={(f) => `${Number(f).toFixed(1)} Hz`} valueFormatter={(v) => `${v.toFixed(4)} ${unit}`} />} />
        <Area type="linear" dataKey="a" name="amplitude" stroke="var(--series-1)" fill="var(--series-1)" fillOpacity={0.18} strokeWidth={1.5} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  )
}

function Vibration({ component, live }) {
  const [wave, setWave] = useState(null)
  const [err, setErr] = useState(null)
  const { version } = useLive()
  useEffect(() => {
    api(`/components/${component.id}/waveform`).then(setWave).catch(setErr)
  }, [component.id, version])

  // real-time mode: the live stream carries every new waveform (raw only - the
  // full analysis below refreshes from the server on the next recorded frame)
  const liveWave = live?.reading?.vibration_waveform
  const waveform = liveWave || wave?.waveform
  const spectrum = useMemo(() => {
    if (!waveform) return []
    if (!liveWave && wave?.spectrum) return wave.spectrum.freq_hz.map((f, i) => ({ f, a: wave.spectrum.amplitude_g[i] })).filter((d) => d.f > 0)
    return dftAmplitude(waveform)
  }, [waveform, wave, liveWave])
  const envelope = useMemo(() => (wave?.envelope_spectrum
    ? wave.envelope_spectrum.freq_hz.map((f, i) => ({ f, a: wave.envelope_spectrum.amplitude_g[i] })).filter((d) => d.f > 0)
    : []), [wave])

  if (err && !waveform) return <div className="card"><div className="empty">No waveform recorded yet.</div></div>
  if (!waveform) return <div className="card"><Loading /></div>
  const series = waveform.map((v, i) => ({ t: i, v }))
  const band = FAULT_BAND[component.type]
  const feats = live?.features || wave?.features || {}
  const an = wave?.analysis
  const lines = (an?.characteristic_lines || []).map((l) => ({ ...l, short: l.name.split(' ')[0] }))
  const specLines = lines.filter((l) => l.domain === 'spectrum')
  const envLines = lines.filter((l) => l.domain === 'envelope')
  return (
    <div className="card">
      <div className="card-head">
        <h3>Waveform analysis</h3>
        <span className="hint">accelerometer, 1 kHz · {liveWave ? 'live frame' : `recorded ${fmt.dateTime(wave?.timestamp)}`}</span>
        <div className="right small secondary mono">RMS {feats.vib_rms?.toFixed(3)} g · kurtosis {feats.vib_kurtosis?.toFixed(2)} · crest {feats.vib_crest?.toFixed(2)}</div>
      </div>
      {an && (
        <div className="callout" style={{ marginBottom: 12, borderLeftColor: LEVEL_COLOR[an.level] }}>
          <strong style={{ color: 'var(--text-primary)' }}>{an.headline}</strong>
          <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
            {an.findings.map((f, i) => <li key={i} className="small">{f}</li>)}
          </ul>
        </div>
      )}
      <div className="grid g3">
        <div>
          <div className="small muted" style={{ marginBottom: 4 }}>Time waveform (g) over {series.length} ms</div>
          <ResponsiveContainer width="100%" height={170}>
            <LineChart data={series} margin={{ top: 6, right: 8, bottom: 0, left: -12 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="t" {...axisProps} tickFormatter={(t) => `${t}`} interval={63} unit=" ms" />
              <YAxis {...axisProps} width={46} tickFormatter={(v) => v.toFixed(2)} />
              <Tooltip content={<ChartTooltip labelFormatter={(t) => `${t} ms`} valueFormatter={(v) => `${v.toFixed(4)} g`} />} />
              <Line type="linear" dataKey="v" name="acceleration" stroke="var(--series-1)" strokeWidth={1.5} dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div>
          <div className="small muted" style={{ marginBottom: 4 }}>FFT spectrum · shaded = defect band {band[0]}–{band[1]} Hz · {Math.round((feats.fault_band_ratio || 0) * 100)}% of energy</div>
          <SpectrumChart data={spectrum} band={band} lines={specLines} />
        </div>
        <div>
          <div className="small muted" style={{ marginBottom: 4 }}>Envelope spectrum (Hilbert demodulation) · impact repetition rates</div>
          {envelope.length ? <SpectrumChart data={envelope} lines={envLines} /> : <div className="empty small">Available on the next recorded frame.</div>}
        </div>
      </div>
      {an && (
        <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 3fr) minmax(0, 2fr)', marginTop: 12, alignItems: 'start' }}>
          <table>
            <thead><tr><th>Characteristic frequency</th><th className="num">Hz</th><th>Where</th><th className="num">Amplitude</th><th className="num">SNR</th><th>State</th></tr></thead>
            <tbody>
              {an.characteristic_lines.map((l) => (
                <tr key={l.name} title={l.meaning}>
                  <td>{l.name}</td>
                  <td className="num mono">{l.hz.toFixed(0)}</td>
                  <td className="small muted">{l.domain}</td>
                  <td className="num mono">{l.amplitude_g.toFixed(4)} g</td>
                  <td className="num mono">{l.snr_db.toFixed(0)} dB</td>
                  <td style={{ color: l.elevated ? 'var(--critical)' : l.detected ? 'var(--warning)' : 'var(--text-muted)' }}>
                    {l.elevated ? 'elevated' : l.detected ? 'visible, normal level' : 'not detected'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div>
            <div className="small muted" style={{ marginBottom: 4 }}>Where the energy sits</div>
            <dl className="kv">
              {Object.entries(an.band_energy_pct).map(([k, v]) => (
                <Fragment key={k}><dt>{k.replace(/_/g, ' ').replace('hz', ' Hz')}</dt><dd className="mono">{v.toFixed(1)}%</dd></Fragment>
              ))}
              <dt>Peak</dt><dd className="mono">{an.stats.peak_g.toFixed(3)} g</dd>
              <dt>Peak-to-peak</dt><dd className="mono">{an.stats.peak_to_peak_g.toFixed(3)} g</dd>
              <dt>Skewness</dt><dd className="mono">{an.stats.skewness.toFixed(2)}</dd>
              <dt>Strongest spectral peaks</dt><dd className="mono small">{an.peaks.slice(0, 3).map((p) => `${p.freq_hz} Hz`).join(' · ')}</dd>
            </dl>
          </div>
        </div>
      )}
    </div>
  )
}

const EFFECT_COLOR = { escalate: 'var(--critical)', hold: 'var(--warning)', cap: 'var(--series-2)', flag: 'var(--text-muted)' }

function RulesPanel({ component }) {
  const rules = component.rules_fired || []
  const phys = component.physics
  return (
    <div className="card">
      <div className="card-head"><h3>Verdict</h3><span className="hint">model → physics → rules</span></div>
      <dl className="kv" style={{ marginBottom: 10 }}>
        <dt>Model verdict</dt><dd><StatusBadge status={component.ml_status || component.status} /></dd>
        <dt>Final status</dt><dd><StatusBadge status={component.status} /> <span className="small muted">via {component.status_source || 'model'}</span></dd>
        <dt>Confidence</dt><dd>{component.confidence || 'high'}</dd>
        {phys && <><dt>Life law</dt><dd title={phys.formula}>{phys.model}</dd></>}
        {phys && <><dt>{phys.label}</dt><dd className="mono">{fmt.pct((phys.life_fraction || 0) * 100)}</dd></>}
        {phys && phys.physics_rul_days != null && <><dt>Remaining life: physics / ML</dt><dd className="mono">{fmt.rul(phys.physics_rul_days)} / {fmt.rul(phys.ml_rul_days)}</dd></>}
      </dl>
      {rules.length === 0 ? <div className="small muted">No rule intervened - the status is the model's own verdict.</div> : rules.map((r, i) => (
        <div className="factor" key={i}>
          <div className="factor-top"><strong style={{ color: EFFECT_COLOR[r.effect] }}>{r.id}</strong><span>{r.name}</span><span className="val small muted">{r.effect}</span></div>
          <div className="factor-advice">{r.message}</div>
        </div>
      ))}
    </div>
  )
}

function RopeTensions({ tensions }) {
  if (!tensions?.length) return null
  const mean = tensions.reduce((a, b) => a + b, 0) / tensions.length
  const data = tensions.map((t, i) => ({ rope: `R${i + 1}`, t, dev: ((t - mean) / mean) * 100 }))
  const maxDev = Math.max(...data.map((d) => Math.abs(d.dev)))
  return (
    <div className="card">
      <div className="card-head"><h3>Per-rope tension</h3><span className="hint">kN · dashed = set average {mean.toFixed(2)} kN</span>
        <div className="right small mono" style={{ color: maxDev > 10 ? 'var(--critical)' : maxDev > 5 ? 'var(--warning)' : 'var(--text-secondary)' }}>max deviation {maxDev.toFixed(1)}% (limit ±10%)</div></div>
      <ResponsiveContainer width="100%" height={190}>
        <BarChart data={data} margin={{ top: 18, right: 8, bottom: 0, left: -12 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="rope" {...axisProps} />
          <YAxis {...axisProps} width={40} domain={[(d) => Math.floor(d * 0.85), (d) => Math.ceil(d * 1.05)]} />
          <ReferenceLine y={mean} stroke="var(--text-secondary)" strokeDasharray="4 4" />
          <Tooltip cursor={{ fill: 'rgba(255,255,255,0.04)' }} content={<ChartTooltip valueFormatter={(v, p) => `${v.toFixed(2)} kN (${p.payload.dev >= 0 ? '+' : ''}${p.payload.dev.toFixed(1)}%)`} />} />
          <Bar dataKey="t" name="tension" radius={[4, 4, 0, 0]} maxBarSize={36} isAnimationActive={false}
            label={{ position: 'top', fill: 'var(--text-secondary)', fontSize: 11, formatter: (v) => `${(((v - mean) / mean) * 100).toFixed(0)}%` }}>
            {data.map((d) => <Cell key={d.rope} fill="var(--series-1)" fillOpacity={Math.abs(d.dev) === maxDev ? 1 : 0.55} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

/** The single recommendation card for this component (Section 6). */
function ComponentRecommendation({ elevatorId, componentId }) {
  const { data } = useLiveFetch(elevatorId ? `/elevators/${elevatorId}/recommendations` : null)
  const rec = data?.cards?.find((c) => c.component_id === componentId)
  if (!rec || rec.action === 'NO ACTION') return null
  return <div style={{ marginBottom: 16 }}><RecommendationCard rec={rec} /></div>
}

export default function ComponentDetail() {
  const { id } = useParams()
  // Kept in the URL so the browser back button and a refresh restore it (Section 2.4).
  const [range, setRange] = useStickyParam('range', 'month')
  const [reload, setReload] = useState(0)
  const { data: c, error } = useLiveFetch(`/components/${id}`, [reload])
  const { data: hist, loading: histLoading } = useLiveFetch(`/components/${id}/history?range=${range}`, [reload])
  const { data: model } = useLiveFetch('/analytics/model')
  const [indicator, setIndicator] = useState(null)
  const [live, setLive] = useState(null)
  const { subscribe, sim } = useLive()

  useEffect(() => subscribe((msg) => {
    if (msg.type === 'component_update' && msg.data.component_id === id && msg.data.mode === 'real-time') setLive(msg.data)
  }), [subscribe, id])
  useEffect(() => { setLive(null); setIndicator(null) }, [id])
  useEffect(() => { if (sim?.mode !== 'real-time') setLive(null) }, [sim?.mode])

  if (error && !c) return <ErrorBox error={error} />
  if (!c) return <Loading />
  const ind = indicator || INDICATORS[c.type][0]
  const th = model?.thresholds?.[c.type]?.[ind]
  const preds = hist?.predictions || []
  const sensors = hist?.sensors || []
  // First point where the series crossed the "replace soon" line - marked on
  // the chart so the user sees WHEN it went bad, not just that it is bad.
  const crossing = preds.find((p) => (p.failure_probability ?? 0) >= THRESHOLD.failureProbability.high)
  const health = live?.health_score ?? c.health_score
  const status = live?.status ?? c.status
  // One shared classification, identical to the fleet list and the cards.
  const level = classify(live?.failure_probability ?? c.failure_probability,
                         live?.rul_days ?? c.rul_days, status)

  return (
    <>
      <BackBar trail={[
        { label: 'Elevators', to: '/elevators' },
        { label: c.elevator?.name, to: `/elevators/${c.elevator_id}` },
        { label: c.name },
      ]} />
      <div className="page-head">
        <HealthRing value={health} size={84} />
        <div>
          <div className="row">
            <CriticalDot level={level} />
            <h1>{c.name}</h1>
            <RiskBadge level={level} />
            <ReplaceTag level={level} />
            {live && <span className="badge live"><span className="dot pulse" style={{ background: 'var(--critical)' }} />live</span>}
          </div>
          <div className="sub">{c.elevator?.name} · {c.elevator?.building} · model {c.model_version}{c.material?.label ? ` · ${c.material.label}` : ''}</div>
        </div>
      </div>

      <div className={`callout ${status}`} style={{ marginBottom: 16 }}>{c.explanation}</div>

      <ComponentRecommendation elevatorId={c.elevator_id} componentId={id} />

      <div className="grid g4" style={{ marginBottom: 16 }}>
        <Stat label="Health score" value={fmt.num(health)} unit="/100" color={healthColor(health)} foot="ML failure risk + physics severity" />
        <Stat label="Failure probability" value={fmt.pct(live?.failure_probability ?? c.failure_probability)} foot="within the next 30 days" />
        <Stat label="Remaining useful life" value={fmt.rul(live?.rul_days ?? c.rul_days)} foot={`~${fmt.num(live?.rul_days ?? c.rul_days)} days (estimate)`} />
        <Stat label={c.physics?.label || 'Life used'} value={fmt.pct((c.fatigue_index || 0) * 100)} foot={c.physics?.model || 'from trips × load'} />
      </div>

      <div className="row" style={{ marginBottom: 12 }}>
        <Seg label="History range" value={range} onChange={setRange} options={[{ value: 'week', label: 'Last 7 days' }, { value: 'month', label: 'Last 30 days' }]} />
        <span className="small muted">{preds.length} predictions · {sensors.length} readings shown</span>
      </div>

      <div className={`grid g2 ${histLoading ? 'loading-frame' : ''}`} style={{ marginBottom: 14 }}>
        <div className="card">
          <div className="card-head"><h3>Health score over time</h3><span className="hint">dashed: at-risk below 45, medium below 72</span></div>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={preds} margin={{ top: 6, right: 12, bottom: 0, left: -18 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="t" {...axisProps} tickFormatter={tickDate} minTickGap={40} />
              <YAxis {...axisProps} domain={[0, 100]} ticks={[0, 25, 45, 72, 100]} />
              <ReferenceLine y={72} stroke="var(--warning)" strokeDasharray="4 4" strokeOpacity={0.6} />
              <ReferenceLine y={45} stroke="var(--critical)" strokeDasharray="4 4" strokeOpacity={0.6} />
              <Tooltip content={<ChartTooltip labelFormatter={(t) => fmt.dateTime(t)} valueFormatter={(v) => `${v.toFixed(1)} / 100`} />} />
              <Line type="monotone" dataKey="health_score" name="health" stroke="var(--series-1)" strokeWidth={2} dot={false} activeDot={{ r: 4 }} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="card">
          <div className="card-head">
            <h3>Failure probability (30-day horizon)</h3>
            <span className="hint">%, dashed lines = shared risk thresholds</span>
            <div className="right legend">
              <span><span className="k" style={{ background: 'var(--critical)' }} />Critical {THRESHOLD.failureProbability.critical}%</span>
              <span><span className="k" style={{ background: 'var(--serious)' }} />High {THRESHOLD.failureProbability.high}%</span>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={preds} margin={{ top: 6, right: 12, bottom: 0, left: -18 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="t" {...axisProps} tickFormatter={tickDate} minTickGap={40} />
              <YAxis {...axisProps} domain={[0, 100]} />
              {/* Section 5.2 - the same thresholds the badges use, drawn on the chart,
                  with the crossing point labelled rather than left to the eye. */}
              <ReferenceLine y={THRESHOLD.failureProbability.high} stroke="var(--serious)" strokeDasharray="4 4" strokeOpacity={0.7}
                label={{ value: 'replace soon', position: 'insideTopLeft', fill: 'var(--serious)', fontSize: 10 }} />
              <ReferenceLine y={THRESHOLD.failureProbability.critical} stroke="var(--critical)" strokeDasharray="4 4" strokeOpacity={0.8}
                label={{ value: 'replace now', position: 'insideTopLeft', fill: 'var(--critical)', fontSize: 10 }} />
              {crossing && <ReferenceLine x={crossing.t} stroke="var(--critical)" strokeOpacity={0.5}
                label={{ value: 'crossed threshold', position: 'top', fill: 'var(--critical)', fontSize: 10 }} />}
              <Tooltip content={<ChartTooltip labelFormatter={(t) => fmt.dateTime(t)} valueFormatter={(v, p) => p.dataKey === 'failure_probability' ? `${v.toFixed(1)}%` : fmt.rul(v)} />} />
              <Area type="monotone" dataKey="failure_probability" name="failure probability" stroke="var(--series-2)" fill="var(--series-2)" fillOpacity={0.15} strokeWidth={2} isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 2fr) minmax(0, 1fr)', alignItems: 'start', marginBottom: 14 }}>
        <div className={`card ${histLoading ? 'loading-frame' : ''}`}>
          <div className="card-head">
            <h3>Condition indicator</h3>
            <div className="right">
              <select value={ind} onChange={(e) => setIndicator(e.target.value)} aria-label="Indicator" style={{ width: 'auto', padding: '4px 8px' }}>
                {INDICATORS[c.type].map((k) => <option key={k} value={k}>{model?.thresholds?.[c.type]?.[k]?.label || k}</option>)}
              </select>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={sensors} margin={{ top: 14, right: 60, bottom: 0, left: -10 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="t" {...axisProps} tickFormatter={tickDate} minTickGap={40} />
              <YAxis {...axisProps} width={52} domain={['auto', 'auto']} tickFormatter={(v) => (Math.abs(v) < 1 ? v.toFixed(2) : v.toFixed(1))} />
              {th && <ReferenceLine y={th.alarm} stroke="var(--critical)" strokeDasharray="4 4" label={{ value: 'alarm', position: 'right', fill: 'var(--text-muted)', fontSize: 11 }} />}
              {th && <ReferenceLine y={th.healthy} stroke="var(--good)" strokeDasharray="4 4" label={{ value: 'healthy', position: 'right', fill: 'var(--text-muted)', fontSize: 11 }} />}
              <Tooltip content={<ChartTooltip labelFormatter={(t) => fmt.dateTime(t)} valueFormatter={(v) => `${Number(v).toFixed(3)} ${th?.unit || ''}`} />} />
              <Line type="monotone" dataKey={ind} name={th?.label || ind} stroke="var(--series-1)" strokeWidth={2} dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div>
          <div className="card" style={{ marginBottom: 14 }}>
            <div className="card-head"><h3>Contributing factors</h3><span className="hint">bar = healthy → alarm (tick)</span></div>
            <FactorList factors={c.contributing_factors} />
          </div>
          <RulesPanel component={live ? { ...c, ...live } : c} />
        </div>
      </div>

      <div style={{ marginBottom: 14 }}><Vibration component={c} live={live} /></div>

      <div className="grid g2">
        <LatestRopeTensions component={c} live={live} />
        <Maintenance component={c} onDone={() => setReload((r) => r + 1)} />
      </div>

      {/* Section 2 - Materials & specification (feeds the physics laws) */}
      <div className="card" style={{ marginTop: 14 }} id="materials">
        <div className="card-head"><h3>Materials &amp; specification</h3><span className="hint">values feed the degradation law; blank = default</span>
          <div className="right"><Link className="btn ghost sm" to={`/elevators/${c.elevator_id}/materials`}>All components of this elevator</Link></div></div>
        <MaterialSection componentId={id} elevatorId={c.elevator_id} onSaved={() => setReload((r) => r + 1)} />
      </div>
    </>
  )
}

function MaterialSection({ componentId, elevatorId, onSaved }) {
  const [rev, setRev] = useState(0)
  const { data } = useLiveFetch(`/components/${componentId}/material-spec`, [rev])
  const { data: e } = useLiveFetch(`/elevators/${elevatorId}`)
  if (!data) return <Loading what="Loading specification" />
  return <MaterialSpecForm componentId={componentId} data={data} driveType={e?.drive_type || 'Gearless PM'} onSaved={() => { setRev((r) => r + 1); onSaved() }} />
}

// Rope-tension bars come from the latest recorded reading (or the live frame).
function LatestRopeTensions({ component, live }) {
  const [tensions, setTensions] = useState(null)
  const { version } = useLive()
  useEffect(() => {
    if (component.type !== 'rope') return
    api(`/components/${component.id}/waveform`).then((w) => setTensions(w.rope_tensions)).catch(() => {})
  }, [component.id, component.type, version])
  if (component.type !== 'rope') return null
  return <RopeTensions tensions={live?.reading?.rope_tensions || tensions} />
}

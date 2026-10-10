import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ReferenceArea, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { api } from '../api'
import { ChartTooltip, ErrorBox, HealthRing, Loading, Seg, Stat, StatusBadge, axisProps } from '../components/ui'
import { fmt, healthColor } from '../format'
import { IconWrench } from '../icons'
import { useLive, useLiveFetch } from '../live'

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

function Maintenance({ component, onDone }) {
  const [busy, setBusy] = useState(false)
  const { pushToast } = useLive()
  const record = async (kind) => {
    if (kind === 'replacement' && !window.confirm(`Record a replacement of "${component.name}"? This resets its wear state.`)) return
    setBusy(true)
    try {
      await api(`/components/${component.id}/maintenance`, { method: 'POST', body: { kind, notes: '' } })
      pushToast({ level: 'info', title: kind === 'replacement' ? 'Replacement recorded' : 'Inspection recorded', text: component.name })
      onDone()
    } catch (e) { pushToast({ level: 'warning', title: 'Could not record maintenance', text: e.message }) }
    finally { setBusy(false) }
  }
  return (
    <div className="card">
      <div className="card-head"><IconWrench style={{ width: 16 }} /><h3>Maintenance</h3></div>
      <dl className="kv" style={{ marginBottom: 12 }}>
        <dt>Installed</dt><dd>{fmt.date(component.install_date)}</dd>
        <dt>Last inspection</dt><dd>{fmt.date(component.last_inspection)}</dd>
        <dt>Next checkup</dt><dd style={{ color: component.checkup_overdue ? 'var(--warning)' : undefined }}>{fmt.date(component.next_checkup_due)}{component.checkup_overdue ? ' (overdue)' : ''}</dd>
        <dt>Fatigue life used</dt><dd>{fmt.pct((component.fatigue_index || 0) * 100)}</dd>
      </dl>
      <div className="row">
        <button className="btn sm" disabled={busy} onClick={() => record('inspection')}>Record inspection</button>
        <button className="btn sm" disabled={busy} onClick={() => record('replacement')}>Record replacement</button>
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
    </div>
  )
}

function Vibration({ component, live }) {
  const [wave, setWave] = useState(null)
  const [err, setErr] = useState(null)
  const { version } = useLive()
  useEffect(() => {
    api(`/components/${component.id}/waveform`).then(setWave).catch(setErr)
  }, [component.id, version])

  // real-time mode: the live stream carries every new waveform
  const waveform = live?.reading?.vibration_waveform || wave?.waveform
  const spectrum = useMemo(() => {
    if (!waveform) return []
    // client-side DFT magnitude for live frames (256 samples - cheap), else server spectrum
    if (!live?.reading?.vibration_waveform && wave?.spectrum) {
      return wave.spectrum.freq_hz.map((f, i) => ({ f, a: wave.spectrum.amplitude_g[i] })).filter((d) => d.f > 0)
    }
    const n = waveform.length
    const mean = waveform.reduce((a, b) => a + b, 0) / n
    const han = (i) => 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (n - 1))
    let wsum = 0
    for (let i = 0; i < n; i++) wsum += han(i)
    const out = []
    for (let k = 1; k <= n / 2; k++) {
      let re = 0, im = 0
      for (let i = 0; i < n; i++) {
        const v = (waveform[i] - mean) * han(i)
        re += v * Math.cos((2 * Math.PI * k * i) / n)
        im -= v * Math.sin((2 * Math.PI * k * i) / n)
      }
      out.push({ f: (k * 1000) / n, a: (2 * Math.sqrt(re * re + im * im)) / wsum })
    }
    return out
  }, [waveform, wave, live])

  if (err && !waveform) return <div className="card"><div className="empty">No waveform recorded yet.</div></div>
  if (!waveform) return <div className="card"><Loading /></div>
  const series = waveform.map((v, i) => ({ t: i, v }))
  const band = FAULT_BAND[component.type]
  const feats = live?.features || wave?.features || {}
  return (
    <div className="card">
      <div className="card-head">
        <h3>Vibration</h3>
        <span className="hint">accelerometer, 1 kHz · {live ? 'live frame' : `recorded ${fmt.dateTime(wave?.timestamp)}`}</span>
        <div className="right small secondary mono">RMS {feats.vib_rms?.toFixed(3)} g · kurtosis {feats.vib_kurtosis?.toFixed(2)} · crest {feats.vib_crest?.toFixed(2)}</div>
      </div>
      <div className="grid g2">
        <div>
          <div className="small muted" style={{ marginBottom: 4 }}>Waveform (g) over 256 ms</div>
          <ResponsiveContainer width="100%" height={180}>
            <LineChart data={series} margin={{ top: 6, right: 8, bottom: 0, left: -12 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="t" {...axisProps} tickFormatter={(t) => `${t}`} interval={63} />
              <YAxis {...axisProps} width={46} tickFormatter={(v) => v.toFixed(2)} />
              <Tooltip content={<ChartTooltip labelFormatter={(t) => `${t} ms`} valueFormatter={(v) => `${v.toFixed(4)} g`} />} />
              <Line type="linear" dataKey="v" name="acceleration" stroke="var(--series-1)" strokeWidth={1.5} dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div>
          <div className="small muted" style={{ marginBottom: 4 }}>FFT spectrum (g) · shaded = {component.type} defect band {band[0]}–{band[1]} Hz · {Math.round((feats.fault_band_ratio || 0) * 100)}% of energy</div>
          <ResponsiveContainer width="100%" height={180}>
            <AreaChart data={spectrum} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="f" type="number" domain={[0, 500]} {...axisProps} tickFormatter={(f) => `${f}`} unit=" Hz" />
              <YAxis {...axisProps} width={50} tickFormatter={(v) => v.toFixed(3)} />
              <ReferenceArea x1={band[0]} x2={band[1]} fill="var(--series-2)" fillOpacity={0.12} stroke="none" />
              <Tooltip content={<ChartTooltip labelFormatter={(f) => `${Number(f).toFixed(1)} Hz`} valueFormatter={(v) => `${v.toFixed(4)} g`} />} />
              <Area type="linear" dataKey="a" name="amplitude" stroke="var(--series-1)" fill="var(--series-1)" fillOpacity={0.18} strokeWidth={1.5} isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>
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

export default function ComponentDetail() {
  const { id } = useParams()
  const [range, setRange] = useState('month')
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
  const health = live?.health_score ?? c.health_score
  const status = live?.status ?? c.status

  return (
    <>
      <div className="crumbs"><Link to="/elevators">Elevators</Link> / <Link to={`/elevators/${c.elevator_id}`}>{c.elevator?.name}</Link> / {c.name}</div>
      <div className="page-head">
        <HealthRing value={health} size={84} />
        <div>
          <div className="row"><h1>{c.name}</h1><StatusBadge status={status} />{live && <span className="badge live"><span className="dot pulse" style={{ background: 'var(--critical)' }} />live</span>}</div>
          <div className="sub">{c.elevator?.name} · {c.elevator?.building} · model {c.model_version}</div>
        </div>
      </div>

      <div className={`callout ${status}`} style={{ marginBottom: 16 }}>{c.explanation}</div>

      <div className="grid g4" style={{ marginBottom: 16 }}>
        <Stat label="Health score" value={fmt.num(health)} unit="/100" color={healthColor(health)} foot="ML failure risk + physics severity" />
        <Stat label="Failure probability" value={fmt.pct(live?.failure_probability ?? c.failure_probability)} foot="within the next 30 days" />
        <Stat label="Remaining useful life" value={fmt.rul(live?.rul_days ?? c.rul_days)} foot={`~${fmt.num(live?.rul_days ?? c.rul_days)} days (estimate)`} />
        <Stat label="Fatigue life used" value={fmt.pct((c.fatigue_index || 0) * 100)} foot="Palmgren–Miner, from trips × load" />
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
          <div className="card-head"><h3>Failure probability (30-day horizon)</h3><span className="hint">%, dashed = 50% at-risk threshold</span></div>
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={preds} margin={{ top: 6, right: 12, bottom: 0, left: -18 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="t" {...axisProps} tickFormatter={tickDate} minTickGap={40} />
              <YAxis {...axisProps} domain={[0, 100]} />
              <ReferenceLine y={50} stroke="var(--critical)" strokeDasharray="4 4" strokeOpacity={0.6} />
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
        <div className="card">
          <div className="card-head"><h3>Contributing factors</h3><span className="hint">bar = healthy → alarm (tick)</span></div>
          <FactorList factors={c.contributing_factors} />
        </div>
      </div>

      <div style={{ marginBottom: 14 }}><Vibration component={c} live={live} /></div>

      <div className="grid g2">
        <LatestRopeTensions component={c} live={live} />
        <Maintenance component={c} onDone={() => setReload((r) => r + 1)} />
      </div>
    </>
  )
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

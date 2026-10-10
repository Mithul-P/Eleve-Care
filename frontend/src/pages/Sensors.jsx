// Section 4 - SENSOR INPUTS per elevator: registry (A), live stream monitor (B), ingestion tools (C).
import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../api'
import BackBar from '../components/BackBar'
import { ChartTooltip, Loading, Seg, Stat, axisProps } from '../components/ui'
import { fmt } from '../format'
import { IconAlert, IconCheck, IconX } from '../icons'
import { useLive, useLiveFetch } from '../live'

const STATUS_COLOR = { online: 'var(--good)', stale: 'var(--warning)', offline: 'var(--text-muted)' }
const EMPTY = { sensor_id: '', name: '', type: 'tension_load_cell', component_id: '', rope_number: '', parameter: 'rope_tension', unit: 'kN', interval_s: 60, location: '', gain: 1, offset: 0 }

function StatusDot({ status }) {
  const Icon = status === 'online' ? IconCheck : status === 'stale' ? IconAlert : IconX
  return <span className="badge sm" style={{ color: STATUS_COLOR[status], borderColor: STATUS_COLOR[status] }}><span className="dot" style={{ background: STATUS_COLOR[status] }} /><Icon />{status}</span>
}

function SensorForm({ meta, reg, initial, onDone, elevatorId }) {
  const { pushToast } = useLive()
  const [f, setF] = useState({ ...EMPTY, ...initial })
  const comp = reg.components.find((c) => c.id === f.component_id)
  const params = meta.types[f.type]?.params || []
  const set = (k, v) => setF((x) => ({ ...x, [k]: v }))
  const submit = async (e) => {
    e.preventDefault()
    try {
      const body = { ...f, rope_number: f.rope_number ? Number(f.rope_number) : null, interval_s: Number(f.interval_s), gain: Number(f.gain), offset: Number(f.offset) }
      if (!params.includes(body.parameter)) body.parameter = params[0]
      await api(`/elevators/${elevatorId}/sensors/${f.sensor_id}`, { method: 'PUT', body })
      pushToast({ level: 'info', title: 'Sensor saved', text: `${f.sensor_id} · ${f.name}` }); onDone(true)
    } catch (err) { pushToast({ level: 'warning', title: 'Could not save sensor', text: err.message }) }
  }
  return (
    <form className="card" onSubmit={submit} style={{ marginTop: 10 }}>
      <div className="grid g4">
        <div className="field"><label>Sensor number / ID</label><input value={f.sensor_id} onChange={(e) => set('sensor_id', e.target.value)} placeholder="S-03" required disabled={!!initial?.sensor_id} /></div>
        <div className="field span2"><label>Sensor name</label><input value={f.name} onChange={(e) => set('name', e.target.value)} placeholder="Rope 3 tension load cell" required /></div>
        <div className="field"><label>Sensor type</label><select value={f.type} onChange={(e) => { const t = e.target.value; set('type', t); set('parameter', meta.types[t].params[0]); set('unit', meta.types[t].unit) }}>{Object.entries(meta.types).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select></div>
        <div className="field"><label>Belongs to component</label><select value={f.component_id} onChange={(e) => set('component_id', e.target.value)} required><option value="">—</option>{reg.components.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></div>
        {comp?.type === 'rope' && f.parameter === 'rope_tension' && (
          <div className="field"><label>Rope number</label><select value={f.rope_number} onChange={(e) => set('rope_number', e.target.value)} required><option value="">—</option>{Array.from({ length: reg.rope_count }, (_, i) => <option key={i + 1} value={i + 1}>R{i + 1}</option>)}</select></div>)}
        <div className="field"><label>Measured parameter</label><select value={f.parameter} onChange={(e) => set('parameter', e.target.value)}>{params.map((p) => <option key={p} value={p}>{p === 'rope_tension' ? 'rope_tensions[i]' : p}</option>)}</select></div>
        <div className="field"><label>Unit</label><input value={f.unit} onChange={(e) => set('unit', e.target.value)} /></div>
        <div className="field"><label>Expected interval (s)</label><input type="number" min="1" value={f.interval_s} onChange={(e) => set('interval_s', e.target.value)} /></div>
        <div className="field span2"><label>Mounting location note</label><input value={f.location} onChange={(e) => set('location', e.target.value)} /></div>
        <div className="field"><label>Calibration gain</label><input type="number" step="any" value={f.gain} onChange={(e) => set('gain', e.target.value)} /></div>
        <div className="field"><label>Calibration offset</label><input type="number" step="any" value={f.offset} onChange={(e) => set('offset', e.target.value)} /></div>
      </div>
      <div className="row"><button className="btn primary" type="submit">Save sensor</button><button className="btn ghost" type="button" onClick={() => onDone(false)}>Cancel</button></div>
    </form>
  )
}

function SensorChart({ elevatorId, sensor, tick }) {
  const { data } = useLiveFetch(`/elevators/${elevatorId}/sensors/${sensor.sensor_id}/series`, [tick])
  if (!data) return <Loading what="Loading series" />
  const pts = data.points.map((p) => ({ t: new Date(p.t).getTime(), v: p.v }))
  const wave = data.waveform ? data.waveform.map((v, i) => ({ i, v })) : null
  const spec = data.spectrum ? data.spectrum.freqs.map((f, i) => ({ f, a: data.spectrum.amp[i] })).filter((x) => x.f > 0) : null
  return (
    <div className="card" style={{ marginTop: 10 }}>
      <div className="card-head"><h3>{sensor.name}</h3><span className="hint">{sensor.sensor_id} · {sensor.parameter} · rolling {pts.length} points</span></div>
      <ResponsiveContainer width="100%" height={180}>
        <LineChart data={pts} margin={{ top: 4, right: 8, left: -14, bottom: 0 }}>
          <CartesianGrid stroke="var(--grid)" strokeDasharray="2 4" /><XAxis dataKey="t" {...axisProps} tickFormatter={(t) => new Date(t).toLocaleTimeString('en-IN', { hour12: false })} minTickGap={50} /><YAxis {...axisProps} domain={['auto', 'auto']} unit={` ${sensor.unit}`} />
          <Tooltip content={<ChartTooltip labelFormatter={(t) => new Date(t).toLocaleString('en-IN')} valueFormatter={(v) => `${v} ${sensor.unit}`} />} />
          <Line type="monotone" dataKey="v" name={sensor.parameter} stroke="var(--series-1)" strokeWidth={1.8} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
      {wave && (
        <div className="grid g2" style={{ marginTop: 8 }}>
          <div><div className="small secondary">Waveform ({wave.length} samples)</div>
            <ResponsiveContainer width="100%" height={150}><LineChart data={wave} margin={{ top: 4, right: 8, left: -14, bottom: 0 }}><CartesianGrid stroke="var(--grid)" strokeDasharray="2 4" /><XAxis dataKey="i" {...axisProps} /><YAxis {...axisProps} unit=" g" /><Tooltip content={<ChartTooltip />} /><Line dataKey="v" name="g" stroke="var(--series-2)" dot={false} isAnimationActive={false} /></LineChart></ResponsiveContainer></div>
          <div><div className="small secondary">FFT amplitude spectrum</div>
            <ResponsiveContainer width="100%" height={150}><LineChart data={spec} margin={{ top: 4, right: 8, left: -14, bottom: 0 }}><CartesianGrid stroke="var(--grid)" strokeDasharray="2 4" /><XAxis dataKey="f" {...axisProps} unit=" Hz" /><YAxis {...axisProps} /><Tooltip content={<ChartTooltip labelFormatter={(f) => `${f.toFixed(0)} Hz`} valueFormatter={(v) => v.toFixed(4)} />} /><Line dataKey="a" name="amplitude" stroke="var(--series-3)" dot={false} isAnimationActive={false} /></LineChart></ResponsiveContainer></div>
        </div>)}
    </div>
  )
}

export default function Sensors() {
  const { id } = useParams()
  const { connected, subscribe, pushToast } = useLive()
  const [tick, setTick] = useState(0)
  const [reload, setReload] = useState(0)
  const [editing, setEditing] = useState(null)
  const [selected, setSelected] = useState(null)
  const [sample, setSample] = useState(null)
  const { data: meta } = useLiveFetch('/sensors/meta')
  const { data: reg } = useLiveFetch(`/elevators/${id}/sensors`, [reload])
  const { data: live } = useLiveFetch(`/elevators/${id}/sensors/live`, [tick])
  const { data: e } = useLiveFetch(`/elevators/${id}`)
  useEffect(() => subscribe((m) => { if (m.type === 'sensor_update' && m.data?.elevator_id === id) setTick((t) => t + 1) }), [subscribe, id])
  useEffect(() => { const t = setInterval(() => setTick((x) => x + 1), 3000); return () => clearInterval(t) }, [])
  const byComp = useMemo(() => {
    if (!live || !reg) return []
    return reg.components.map((c) => ({
      ...c,
      sensors: live.sensors.filter((s) => s.component_id === c.id),
    }))
  }, [live, reg])
  if (!meta || !reg || !live || !e) return <Loading what="Loading sensors" />

  const setSource = async (source) => { await api(`/elevators/${id}/sensors/source`, { method: 'PUT', body: { source } }); setReload((r) => r + 1); setTick((t) => t + 1) }
  const remove = async (sid) => { if (!confirm(`Delete sensor ${sid}?`)) return; await api(`/elevators/${id}/sensors/${sid}`, { method: 'DELETE' }); setReload((r) => r + 1); setTick((t) => t + 1) }
  const registerDefaults = async () => { const r = await api(`/elevators/${id}/sensors/defaults`, { method: 'POST' }); pushToast({ level: 'info', title: 'Default sensors registered', text: `${r.added} added` }); setReload((x) => x + 1); setTick((t) => t + 1) }
  const test = async (sid) => { const r = await api(`/elevators/${id}/sensors/${sid}/test`, { method: 'POST' }); pushToast({ level: r.result.accepted ? 'info' : 'warning', title: `Test reading → ${sid}`, text: r.result.accepted ? (r.result.reading_ingested ? 'Accepted and a full reading was assembled → prediction updated.' : 'Accepted; waiting for the other parameters of this component to complete the reading.') : r.result.reason }); setTick((t) => t + 1) }
  const showSample = async (sid) => setSample(await api(`/elevators/${id}/sensors/${sid}/sample`))
  const missing = reg.coverage.filter((c) => !c.complete)

  return (
    <div className="sensor-page">
      <BackBar trail={[{ label: 'Elevators', to: '/elevators' }, { label: e.name, to: `/elevators/${id}` }, { label: 'Sensor inputs' }]} />
      <div className="page-head sensor-head">
        <div>
          <h1>Sensor inputs</h1>
          <div className="sub">{e.name} · <span className="lift-type">Traction lift</span> · registry, live stream and ingestion tools</div>
        </div>
        <div className="actions">
          <span className="small muted">Source</span>
          <Seg label="Sensor source" value={live.source} onChange={setSource} options={[{ value: 'simulated', label: 'Simulated' }, { value: 'external', label: 'External gateway' }]} />
          {live.source === 'external' ? (
            <span className={`badge ${live.gateway_connected ? 'good' : 'at-risk'}`}>
              <span className="dot" style={{ background: live.gateway_connected ? 'var(--good)' : 'var(--critical)' }} />
              {live.gateway_status}
            </span>
          ) : (
            <span className="badge neutral"><span className="dot" style={{ background: connected ? 'var(--good)' : 'var(--critical)' }} />WebSocket {connected ? 'connected' : 'disconnected'}</span>
          )}
        </div>
      </div>

      {/* Gateway Disconnected Warning Banner (Section 4) */}
      {live.source === 'external' && !live.gateway_connected && (
        <div className="risk-banner risk-high" style={{ cursor: 'default', marginBottom: 16 }}>
          <IconAlert />
          <div>
            <strong>Gateway disconnected — data may not be current</strong>
            <div className="small secondary" style={{ marginTop: 2 }}>
              No sensor data has arrived within the expected interval. Verify the external gateway connection or ingest telemetry.
            </div>
          </div>
        </div>
      )}

      {/* KPI Stats */}
      <div className="grid g4 stat-grid" style={{ marginBottom: 16 }}>
        <Stat label="Online" value={live.summary.online} color="var(--good)" foot={`of ${live.summary.total} sensors`} />
        <Stat label="Stale" value={live.summary.stale} color={live.summary.stale ? 'var(--warning)' : undefined} foot="no message within 2× interval" />
        <Stat label="Offline" value={live.summary.offline} color={live.summary.offline ? 'var(--critical)' : undefined} foot="no message within 10× interval" />
        <Stat label="Message rate" value={live.summary.rate_per_min} unit="/ min" foot={`stream clients ${live.stream_clients}`} />
      </div>

      {/* Coverage Status Bar */}
      {missing.length > 0 ? (
        <div className="risk-banner risk-high" style={{ cursor: 'default', marginBottom: 16 }}>
          <IconAlert />
          <strong>Missing sensors</strong>
          <span className="small secondary">{missing.map((m) => `${m.component_name}: ${m.missing.join(', ')}`).join(' · ')}</span>
        </div>
      ) : (
        <div className="coverage-bar" style={{ marginBottom: 16 }}>
          <div className="coverage-bar-left">
            <span className="dot" style={{ background: 'var(--good)' }} />
            <strong>Coverage checklist:</strong>
            <span className="secondary small">All {reg.components.length} components covered ({reg.sensors.length} sensors · {reg.rope_count} suspension ropes)</span>
          </div>
          <div className="coverage-tags">
            {reg.coverage.map((c) => (
              <span key={c.component_id} className="coverage-tag good" title={c.items.map((i) => `${i.label} (${i.detail})`).join('\n')}>
                ✓ {c.component_name}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Full-width Unified Sensor Registry & Live Monitor */}
      <div className="card full-width-card">
        <div className="card-head">
          <div>
            <h3>Sensor registry</h3>
            <span className="hint">Live stream monitor · {live.sensors.length} registered sensors · click row to expand rolling chart, waveform &amp; FFT spectrum</span>
          </div>
          <div className="right">
            <button className="btn sm" onClick={registerDefaults}>Register standard set</button>
            <button className="btn primary sm" onClick={() => setEditing({})}>+ Add sensor</button>
          </div>
        </div>

        {editing && (
          <SensorForm meta={meta} reg={reg} initial={editing} elevatorId={id} onDone={(saved) => { setEditing(null); if (saved) { setReload((r) => r + 1); setTick((t) => t + 1) } }} />
        )}

        <div className="scroll-x">
          <table className="sensor-table">
            <thead>
              <tr>
                <th style={{ width: 100 }}>Status</th>
                <th style={{ width: 75 }}>ID</th>
                <th>Sensor</th>
                <th>Type</th>
                <th>Component</th>
                <th>Parameter</th>
                <th style={{ width: 85 }}>Interval</th>
                <th style={{ width: 120 }}>Last Value</th>
                <th style={{ width: 100 }}>Last Seen</th>
                <th style={{ textAlign: 'right', minWidth: 260 }}>Action</th>
              </tr>
            </thead>
            <tbody>
              {byComp.map((c) => (
                <FragmentGroup key={c.id}>
                  <tr className="sensor-group-header">
                    <td colSpan={10}>
                      <span className="sensor-group-title">{c.name}</span>
                      <span className="muted small" style={{ marginLeft: 8 }}>({c.sensors.length} sensors)</span>
                    </td>
                  </tr>
                  {c.sensors.map((s) => {
                    const isSelected = selected === s.sensor_id
                    return (
                      <tr key={s.sensor_id} className={`sensor-row ${isSelected ? 'row-selected' : ''}`} onClick={() => setSelected(isSelected ? null : s.sensor_id)}>
                        <td><StatusDot status={s.status} /></td>
                        <td className="mono">{s.sensor_id}</td>
                        <td>
                          <span className="sensor-name-text">{s.name}</span>
                          {s.rope_number ? <span className="unit-tag">R{s.rope_number}</span> : null}
                        </td>
                        <td className="small muted">{meta.types[s.type]?.label || s.type}</td>
                        <td className="small secondary"><Link to={`/components/${c.id}`} onClick={(e) => e.stopPropagation()} style={{ color: 'var(--accent)', textDecoration: 'none' }}>{c.name}</Link></td>
                        <td className="small mono">{s.parameter}{s.unit ? ` (${s.unit})` : ''}</td>
                        <td className="mono small">{s.interval_s}s</td>
                        <td className="mono sensor-val-cell">{s.last_value == null ? '—' : `${s.last_value} ${s.unit}`}</td>
                        <td className="small muted sensor-ago-cell">{fmt.ago(s.last_seen)}</td>
                        <td style={{ textAlign: 'right' }} onClick={(ev) => ev.stopPropagation()}>
                          <div className="row sensor-actions" style={{ justifyContent: 'flex-end', gap: 4 }}>
                            <Link to={`/components/${c.id}`} className="btn ghost sm" title="Navigate to component detail">View</Link>
                            <button className="btn ghost sm" title="Send live reading" onClick={() => test(s.sensor_id)}>Test</button>
                            <button className="btn ghost sm" title="Edit sensor configuration" onClick={() => setEditing(s)}>Edit</button>
                            <button className="btn ghost sm" title="View sample JSON & curl command" onClick={() => showSample(s.sensor_id)}>JSON</button>
                            <button className="btn ghost sm" title="Delete sensor" onClick={() => remove(s.sensor_id)}>Delete</button>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </FragmentGroup>
              ))}
            </tbody>
          </table>
        </div>
        {live.sensors.length === 0 && (
          <div className="empty">No sensors registered. <button className="btn sm" onClick={registerDefaults}>Register the standard sensor set</button></div>
        )}
      </div>

      {/* Selected Sensor Chart */}
      {selected && live.sensors.find((s) => s.sensor_id === selected) && (
        <SensorChart elevatorId={id} sensor={live.sensors.find((s) => s.sensor_id === selected)} tick={tick} />
      )}

      {/* Ingestion Tools */}
      <div className="card" style={{ marginTop: 16 }}>
        <div className="card-head">
          <h3>Ingestion &amp; test tools</h3>
          <span className="hint">POST /ingest/sensor-data (header X-Ingest-Key) · WebSocket /ws/ingest/{id}?key=…</span>
        </div>
        <div className="small secondary" style={{ lineHeight: 1.6 }}>
          One message per sensor: <code className="mono">{'{elevator_id, sensor_id, timestamp, value | values[] | waveform[]}'}</code>.
          The backend maps the sensor to its component and parameter, buffers values in a 10-second bucket, assembles the standard SensorReading (rope tensions from per-rope load cells, waveform 32–4096 samples) and hands it to the ingestion pipeline.
        </div>
        {live.unregistered.length > 0 && (
          <div className="small" style={{ marginTop: 10 }}>
            <strong style={{ color: 'var(--warning)' }}>Unregistered sensor IDs seen:</strong> {live.unregistered.map((u) => `${u.sensor_id} (${fmt.ago(u.at)})`).join(', ')}
          </div>
        )}
        {sample && (
          <div style={{ marginTop: 14 }}>
            <div className="row" style={{ marginBottom: 6 }}>
              <strong>Sample payload for {sample.json.sensor_id}</strong>
              <button className="btn ghost sm" onClick={() => navigator.clipboard?.writeText(JSON.stringify(sample.json, null, 2))}>Copy JSON</button>
              <button className="btn ghost sm" onClick={() => navigator.clipboard?.writeText(sample.curl)}>Copy curl</button>
            </div>
            <pre className="code-block">{JSON.stringify(sample.json, null, 2)}</pre>
            <pre className="code-block">{sample.curl}</pre>
            <div className="small muted">{sample.websocket}</div>
          </div>
        )}
      </div>
    </div>
  )
}

function FragmentGroup({ children }) {
  return <>{children}</>
}

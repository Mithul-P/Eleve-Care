import { useState } from 'react'
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { useParams } from 'react-router-dom'
import BackBar, { useStickyParam } from '../components/BackBar'
import { ChartTooltip, ErrorBox, Loading, Seg, Stat, axisProps } from '../components/ui'
import { fmt } from '../format'
import { useLiveFetch } from '../live'

// Section 3 - KONE-style operational data inputs.
//
// The seven inputs KONE collects per elevator, each with a summary card, a
// trend chart and a table. All of it comes from /elevators/{id}/operational,
// which is generated deterministically per (elevator, date) - so these numbers
// match the energy page, the cycles page and the chatbot exactly, and past
// days never change.
//
// The last card set shows how these operational inputs feed the risk score,
// which is the "why is this component at risk?" requirement.

const WINDOWS = [
  { value: '7', label: '7 days' },
  { value: '30', label: '30 days' },
  { value: '90', label: '90 days' },
]

const METRICS = [
  { key: 'trips', label: 'Number of trips', unit: 'trips', color: 'var(--series-1)' },
  { key: 'door_movements', label: 'Door movements', unit: 'open/close cycles', color: 'var(--series-3)' },
  { key: 'car_calls', label: 'Car calls', unit: 'requests from inside the car', color: 'var(--accent)' },
  { key: 'power_ups', label: 'Power-ups', unit: 'controller restarts', color: 'var(--series-2)' },
]

const SEVERITIES = [
  { value: 'all', label: 'All' },
  { value: 'critical', label: 'Critical' },
  { value: 'warning', label: 'Warning' },
  { value: 'info', label: 'Info' },
]

const SEV_CLASS = { critical: 'risk-critical', warning: 'risk-medium', info: '' }

function MetricBlock({ metric, trend, total, days }) {
  return (
    <div className="card">
      <div className="card-head">
        <h3>{metric.label}</h3>
        <span className="hint">{metric.unit}</span>
      </div>
      <div className="grid g2" style={{ marginBottom: 12 }}>
        <Stat label={`Total (${days} d)`} value={fmt.num(total)} />
        <Stat label="Average per day" value={fmt.num(total / Math.max(days, 1), 1)} />
      </div>
      <ResponsiveContainer width="100%" height={140}>
        <LineChart data={trend} margin={{ top: 4, right: 6, bottom: 0, left: -12 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="date" {...axisProps} tickFormatter={fmt.dateShort} minTickGap={36} />
          <YAxis {...axisProps} width={44} />
          <Tooltip content={<ChartTooltip labelFormatter={fmt.date} valueFormatter={(v) => fmt.num(v)} />} />
          <Line type="monotone" dataKey={metric.key} name={metric.label} stroke={metric.color}
            strokeWidth={2} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

function ErrorTable({ errors }) {
  // Sortable and filterable by severity, per Section 3.
  const [severity, setSeverity] = useStickyParam('sev', 'all')
  const [sort, setSort] = useState({ key: 'timestamp', dir: 'desc' })

  const rank = { critical: 0, warning: 1, info: 2 }
  let rows = severity === 'all' ? errors : errors.filter((e) => e.severity === severity)
  rows = [...rows].sort((a, b) => {
    const dir = sort.dir === 'asc' ? 1 : -1
    if (sort.key === 'severity') return (rank[a.severity] - rank[b.severity]) * dir
    if (sort.key === 'code') return a.code.localeCompare(b.code) * dir
    if (sort.key === 'resolved') return ((a.resolved ? 1 : 0) - (b.resolved ? 1 : 0)) * dir
    return a.timestamp.localeCompare(b.timestamp) * dir
  })

  const toggle = (key) =>
    setSort((s) => ({ key, dir: s.key === key && s.dir === 'desc' ? 'asc' : 'desc' }))
  const arrow = (key) => (sort.key === key ? (sort.dir === 'desc' ? ' \u25BC' : ' \u25B2') : '')

  return (
    <div className="card">
      <div className="card-head">
        <h3>Error codes</h3>
        <span className="hint">{rows.length} of {errors.length} events</span>
        <div className="right">
          <Seg label="Severity" value={severity} onChange={setSeverity} options={SEVERITIES} />
        </div>
      </div>
      {rows.length === 0 ? <div className="empty">No error codes at this severity.</div> : (
        <div className="scroll-y">
          <table>
            <thead>
              <tr>
                <th className="sortable" onClick={() => toggle('timestamp')}>Time{arrow('timestamp')}</th>
                <th className="sortable" onClick={() => toggle('code')}>Code{arrow('code')}</th>
                <th>Description</th>
                <th>Component</th>
                <th className="sortable" onClick={() => toggle('severity')}>Severity{arrow('severity')}</th>
                <th className="sortable" onClick={() => toggle('resolved')}>Resolved{arrow('resolved')}</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 200).map((e) => (
                <tr key={e.id} className={e.severity === 'critical' && !e.resolved ? 'row-critical' : ''}>
                  <td className="mono small">{fmt.dateTime(e.timestamp)}</td>
                  <td className="mono">{e.code}</td>
                  <td>{e.description}</td>
                  <td className="small secondary">{e.subsystem}</td>
                  <td><span className={`badge ${SEV_CLASS[e.severity] || 'neutral'}`}>{e.severity}</span></td>
                  <td>{e.resolved ? <span className="muted small">closed</span> : <strong className="small">open</strong>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export default function Operational() {
  const { id } = useParams()
  const [days, setDays] = useStickyParam('days', '30')
  const { data, error } = useLiveFetch(`/elevators/${id}/operational?days=${days}`)

  const loadingTrail = [
    { label: 'Elevators', to: '/elevators' },
    { label: 'Operational data', to: `/elevators/${id}` },
  ]
  if (error && !data) return (
    <>
      <BackBar trail={loadingTrail} />
      <ErrorBox error={error} />
    </>
  )
  if (!data) return (
    <>
      <BackBar trail={loadingTrail} />
      <Loading />
    </>
  )

  const d = Number(days)
  const t = data.totals
  const ec = data.error_counts

  return (
    <>
      <BackBar trail={[
        { label: 'Elevators', to: '/elevators' },
        { label: data.elevator_name, to: `/elevators/${id}` },
        { label: 'Operational data' },
      ]} />

      <div className="page-head">
        <div>
          <h1>Operational data</h1>
          <div className="sub">{data.elevator_name} - the seven operational inputs collected from the controller, over the last {d} days.</div>
        </div>
        <div className="right"><Seg label="Window" value={days} onChange={setDays} options={WINDOWS} /></div>
      </div>

      {/* 1-4: the four counted inputs, as summary cards */}
      <div className="grid g4" style={{ marginBottom: 14 }}>
        <Stat label="Door movements" value={fmt.num(t.door_movements)} foot={`${fmt.num(t.door_movements / d, 0)} per day`} />
        <Stat label="Number of trips" value={fmt.num(t.trips)} foot={`${fmt.num(t.trips / d, 0)} per day`} />
        <Stat label="Car calls" value={fmt.num(t.car_calls)} foot={`${fmt.num(t.car_calls / Math.max(t.trips, 1), 2)} per trip`} />
        <Stat label="Power-ups" value={fmt.num(t.power_ups)} foot="controller restarts"
          color={t.power_ups > d / 5 ? 'var(--warning)' : undefined} />
      </div>

      <div className="grid g4" style={{ marginBottom: 14 }}>
        <Stat label="Error codes" value={fmt.num(ec.total)} foot={`${ec.unresolved} unresolved`}
          color={ec.critical ? 'var(--critical)' : undefined} />
        <Stat label="Critical faults" value={fmt.num(ec.critical)} foot="highest severity"
          color={ec.critical ? 'var(--critical)' : undefined} />
        <Stat label="Connected systems" value={data.connected_systems.length} foot="reporting performance data" />
        <Stat label="Operational risk uplift" value={`+${fmt.num(data.risk.uplift_pct, 1)}`} unit="pp"
          foot="added to failure probability" color={data.risk.uplift_pct > 5 ? 'var(--serious)' : undefined} />
      </div>

      {/* 5-7 plus trends */}
      <div className="grid g2" style={{ alignItems: 'start', marginBottom: 14 }}>
        {METRICS.map((m) => (
          <MetricBlock key={m.key} metric={m} trend={data.trend} total={t[m.key]} days={d} />
        ))}
      </div>

      <div style={{ marginBottom: 14 }}><ErrorTable errors={data.errors} /></div>

      <div className="grid g2" style={{ alignItems: 'start', marginBottom: 14 }}>
        <div className="card">
          <div className="card-head">
            <h3>Connected components &amp; systems</h3>
            <span className="hint">performance index, 100 = as new</span>
          </div>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={data.connected_systems} margin={{ top: 6, right: 8, bottom: 0, left: -10 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="system" {...axisProps} />
              <YAxis {...axisProps} width={44} domain={[0, 100]} />
              <Tooltip cursor={{ fill: 'rgba(255,255,255,0.04)' }}
                content={<ChartTooltip valueFormatter={(v) => `${v} / 100`} />} />
              <Bar dataKey="performance_index" name="performance" fill="var(--series-1)"
                radius={[4, 4, 0, 0]} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
          <table>
            <thead><tr><th>System</th><th className="num">Index</th><th className="num">Errors</th><th className="num">Availability</th></tr></thead>
            <tbody>
              {data.connected_systems.map((s) => (
                <tr key={s.system}>
                  <td>{s.system}</td>
                  <td className="num">{fmt.num(s.performance_index, 1)}</td>
                  <td className="num">{s.errors_30d}</td>
                  <td className="num">{fmt.num(s.availability_pct, 2)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="card">
          <div className="card-head">
            <h3>Why is this at risk?</h3>
            <span className="hint">operational inputs driving the score</span>
          </div>
          <p className="secondary small" style={{ marginTop: 0 }}>
            These inputs add <strong>+{fmt.num(data.risk.uplift_pct, 1)} percentage points</strong> to the
            failure probability produced by the physics and ML model. The uplift is capped at +12 pp so
            operational noise can nudge, but never dominate, the prediction.
          </p>
          {data.risk.factors.map((f) => (
            <div className="factor" key={f.key}>
              <div className="factor-top">
                <span>{f.label}</span>
                <span className="mono small">{f.display_value}</span>
              </div>
              <div className="sev-track">
                <span className="sev-fill" style={{
                  width: `${Math.max(2, f.load * 100)}%`,
                  background: f.load > 0.66 ? 'var(--critical)' : f.load > 0.33 ? 'var(--warning)' : 'var(--good)',
                }} />
              </div>
              <div className="factor-advice small muted">
                weight {Math.round(f.weight * 100)}% of the operational uplift
              </div>
            </div>
          ))}
        </div>
      </div>

      <ul className="assumptions">
        <li>Operational counts are generated deterministically per elevator and date: past days are frozen and identical on every load.</li>
        <li>Every field is timestamped and stamped with the elevator ID, so a real KONE telemetry feed can replace the generator without changing this page.</li>
        <li>Error frequency rises with modelled degradation, which is what makes the error count a meaningful risk input rather than decoration.</li>
      </ul>
    </>
  )
}

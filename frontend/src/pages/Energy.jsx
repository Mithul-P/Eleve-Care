import { useState } from 'react'
import { Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import BackBar, { useStickyParam } from '../components/BackBar'
import ElevatorSelect from '../components/ElevatorSelect'
import { ChartTooltip, ErrorBox, Loading, Seg, Stat, StatusBadge, axisProps } from '../components/ui'
import { fmt } from '../format'
import { IconAlert, IconCheck, IconX } from '../icons'
import { useLiveFetch } from '../live'

const VIEWS = [
  { value: 'trend', label: 'Daily trend' },
  { value: 'week', label: 'Week-wise' },
  { value: 'month', label: 'Month-wise' },
  { value: 'year', label: 'Year-wise' },
]

const CALENDAR_TITLE = { week: 'Weekly energy cost', month: 'Monthly energy cost', year: 'Yearly energy cost' }
const CALENDAR_UNIT = { week: 'week', month: 'month', year: 'year' }

const STATE_LABEL = {
  complete: 'Complete',
  'week-to-date': 'Week to date',
  'month-to-date': 'Month to date',
  'year-to-date': 'Year to date',
  running: 'In progress',
  upcoming: 'Upcoming',
}

/** Periods that have not happened yet are drawn muted, never as real data. */
const barColor = (state) =>
  state === 'upcoming' ? 'var(--surface-3)'
    : state === 'running' || state === 'month-to-date' || state === 'week-to-date' || state === 'year-to-date' ? 'var(--series-3)'
      : 'var(--series-1)'

function StateTag({ state }) {
  if (!state || state === 'complete') return null
  return <span className={`state-tag ${state === 'upcoming' ? 'upcoming' : 'partial'}`}>{STATE_LABEL[state]}</span>
}

function CalendarView({ view, elevator }) {
  const path = `/analytics/energy/calendar?view=${view}${elevator ? `&elevator_id=${elevator}` : ''}`
  const { data, error, loading } = useLiveFetch(path)
  const [selected, setSelected] = useState(null)

  if (error && !data) return <ErrorBox error={error} />
  if (!data) return <Loading />

  const rows = data.rows
  const keyOf = (r) => (view === 'month' ? r.month : view === 'week' ? r.week : view === 'year' ? String(r.year) : r.label)
  const active = rows.find((r) => keyOf(r) === selected) || null
  const t = data.totals

  return (
    <div className={loading ? 'loading-frame' : ''}>
      <div className="grid g4" style={{ marginBottom: 14 }}>
        <Stat label="Energy consumed" value={fmt.num(t.energy_kwh)} unit="kWh" foot={data.period_label} />
        <Stat label="Energy cost" value={fmt.inr(t.cost_inr)} foot={`at INR ${data.tariff_inr_per_kwh}/unit`} />
        <Stat label="Wasted by degradation" value={fmt.inr(t.excess_cost_inr)}
          foot={`${fmt.num(t.excess_kwh, 1)} kWh above healthy baseline`} color="var(--serious)" />
        <Stat label="Trips" value={fmt.num(t.trips)} foot={`${fmt.num((t.energy_kwh / Math.max(t.trips, 1)) * 1000, 0)} Wh per trip`} />
      </div>

      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-head">
          <h3>{CALENDAR_TITLE[view] || 'Energy cost'}</h3>
          <span className="hint">kWh - {data.period_label}</span>
          <div className="right legend">
            <span><span className="k" style={{ background: 'var(--series-1)' }} />Frozen</span>
            <span><span className="k" style={{ background: 'var(--series-3)' }} />In progress</span>
            <span><span className="k" style={{ background: 'var(--surface-3)' }} />Upcoming</span>
          </div>
        </div>
        <ResponsiveContainer width="100%" height={250}>
          <BarChart data={rows} margin={{ top: 6, right: 8, bottom: 0, left: -10 }}
            onClick={(e) => e?.activeLabel && setSelected(view === 'month' ? (rows.find((r) => r.label === e.activeLabel) || {}).month : view === 'week' ? (rows.find((r) => r.label === e.activeLabel) || {}).week : view === 'year' ? String((rows.find((r) => r.label === e.activeLabel) || {}).year) : e.activeLabel)}>
            <CartesianGrid stroke="var(--grid)" vertical={false} />
            <XAxis dataKey="label" {...axisProps} interval={0} angle={-30}
              textAnchor="end" height={52} />
            <YAxis {...axisProps} width={52} />
            <Tooltip cursor={{ fill: 'rgba(255,255,255,0.04)' }}
              content={<ChartTooltip
                labelFormatter={(l, p) => `${l} - ${fmt.inr(p?.[0]?.payload?.cost_inr)}`}
                valueFormatter={(v) => `${Number(v).toFixed(1)} kWh`} />} />
            <Bar dataKey="energy_kwh" name="energy" radius={[4, 4, 0, 0]} isAnimationActive={false}>
              {rows.map((r) => <Cell key={keyOf(r)} fill={barColor(r.state)} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
        <div className="hint" style={{ marginTop: 8 }}>
          Click a {CALENDAR_UNIT[view] || 'period'} to inspect it. Completed periods are frozen - identical across calls and restarts.
        </div>
      </div>

      {active && (
        <div className="card" style={{ marginBottom: 14 }}>
          <div className="card-head">
            <h3>{active.label} detail</h3>
            <StateTag state={active.state} />
            <div className="right">
              <button className="btn sm ghost" onClick={() => setSelected(null)}>Close</button>
            </div>
          </div>
          <div className="grid g4">
            <Stat label="Total energy" value={fmt.num(active.energy_kwh)} unit="kWh" />
            <Stat label="Healthy baseline" value={fmt.num(active.baseline_kwh)} unit="kWh" />
            <Stat label="Degradation excess" value={fmt.num(active.energy_kwh - active.baseline_kwh, 2)}
              unit="kWh" color="var(--serious)" />
            <Stat label="Total cost" value={fmt.inr(active.cost_inr)} />
          </div>
        </div>
      )}

      {!elevator && data.per_elevator?.length > 0 && (
        <div className="card">
          <div className="card-head">
            <h3>Per-elevator breakdown</h3>
            <span className="hint">sorted by energy consumption</span>
          </div>
          <div className="scroll-x">
            <table>
              <thead>
                <tr>
                  <th>Elevator</th>
                  <th className="num">Energy</th>
                  <th className="num">Baseline</th>
                  <th className="num">Cost</th>
                  <th className="num">Trips</th>
                </tr>
              </thead>
              <tbody>
                {data.per_elevator.map((e) => (
                  <tr key={e.elevator_id}>
                    <td>{e.name}</td>
                    <td className="num">{fmt.num(e.energy_kwh, 1)} kWh</td>
                    <td className="num">{fmt.num(e.baseline_kwh, 1)} kWh</td>
                    <td className="num">{fmt.inr(e.cost_inr)}</td>
                    <td className="num">{fmt.num(e.trips)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

function MetricModal({ metricKey, info, onClose }) {
  if (!info) return null
  return (
    <div className="modal-backdrop" onClick={onClose} style={{
      position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
      background: 'rgba(0,0,0,0.65)', zIndex: 1000,
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20
    }}>
      <div className="card modal-content" onClick={(e) => e.stopPropagation()} style={{
        maxWidth: 620, width: '100%', maxHeight: '90vh', overflowY: 'auto',
        border: '1px solid var(--border-strong)', boxShadow: '0 12px 32px rgba(0,0,0,0.5)'
      }}>
        <div className="card-head" style={{ borderBottom: '1px solid var(--border)', paddingBottom: 12 }}>
          <div>
            <h3 style={{ margin: 0, color: 'var(--text-primary)' }}>{info.title}</h3>
            <span className="hint">Savings analysis metric methodology</span>
          </div>
          <button className="btn sm ghost" onClick={onClose} aria-label="Close modal">
            <IconX style={{ width: 16 }} />
          </button>
        </div>

        <div className="stack" style={{ gap: 14, marginTop: 14 }}>
          <div>
            <strong className="small muted">WHAT IT MEANS</strong>
            <div style={{ marginTop: 3, lineHeight: 1.5 }}>{info.meaning}</div>
          </div>
          <div>
            <strong className="small muted">SOURCE</strong>
            <div style={{ marginTop: 3, lineHeight: 1.5 }} className="secondary">{info.source}</div>
          </div>
          <div>
            <strong className="small muted">CALCULATION &amp; FORMULA</strong>
            <div style={{ marginTop: 3, lineHeight: 1.5 }}>{info.calculation}</div>
            {info.formula && (
              <pre className="code-block" style={{ margin: '6px 0 0', padding: '8px 12px' }}>{info.formula}</pre>
            )}
          </div>
          <div>
            <strong className="small muted">WHY IT MATTERS</strong>
            <div style={{ marginTop: 3, lineHeight: 1.5 }}>{info.why_it_matters}</div>
          </div>
          <div>
            <strong className="small muted">INTERPRETATION</strong>
            <div style={{ marginTop: 3, lineHeight: 1.5 }} className="secondary">{info.interpretation}</div>
          </div>
        </div>

        <div style={{ marginTop: 20, textAlign: 'right' }}>
          <button className="btn primary sm" onClick={onClose}>Done</button>
        </div>
      </div>
    </div>
  )
}

function TrendView({ range, elevator }) {
  const path = elevator ? `/analytics/energy?range=${range}&elevator_id=${elevator}` : `/analytics/energy?range=${range}`
  const { data, error, loading } = useLiveFetch(path)
  const [activeMetric, setActiveMetric] = useState(null)

  if (error && !data) return <ErrorBox error={error} />
  if (!data) return <Loading />
  const m = data.maintenance
  const explanations = m?.metric_explanations || {}

  const openExpl = (key) => {
    if (explanations[key]) setActiveMetric(key)
  }

  return (
    <div className={loading ? 'loading-frame' : ''}>
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-head">
          <h3>Daily energy cost</h3>
          <span className="hint">INR at {data.tariff_inr_per_kwh}/kWh · last 30 days</span>
        </div>
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={data.daily} margin={{ top: 6, right: 8, bottom: 0, left: -6 }}>
            <CartesianGrid stroke="var(--grid)" vertical={false} />
            <XAxis dataKey="date" {...axisProps} tickFormatter={fmt.dateShort} minTickGap={30} />
            <YAxis {...axisProps} width={52} tickFormatter={(v) => `${v}`} />
            <Tooltip content={<ChartTooltip labelFormatter={fmt.date} valueFormatter={(v) => fmt.inr(v)} />} />
            <Line type="monotone" dataKey="cost_inr" name="energy cost" stroke="var(--series-1)" strokeWidth={2} dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* Visually prominent Savings Analysis section title */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-head" style={{ marginBottom: 12 }}>
          <div>
            <h2 style={{ fontSize: '1.25rem', fontWeight: 700, letterSpacing: '-0.02em', margin: '0 0 4px 0', color: 'var(--text-primary)' }}>
              Savings Analysis — Predictive vs Reactive Maintenance
            </h2>
            <span className="hint">Click any metric or column heading for complete formula and derivation details</span>
          </div>
        </div>

        <div className="grid g3" style={{ marginBottom: 16 }}>
          <div className="clickable-stat" onClick={() => openExpl('avoided_repair_cost')} style={{ cursor: 'pointer' }} title="Click for calculation details">
            <Stat label="Avoided repair cost (flagged parts) ⓘ" value={fmt.inr(m.total_avoided_inr)} foot="planned replacement instead of in-service failure" />
          </div>
          <div className="clickable-stat" onClick={() => openExpl('risk_weighted_saving')} style={{ cursor: 'pointer' }} title="Click for calculation details">
            <Stat label="Risk-weighted saving ⓘ" value={fmt.inr(m.risk_weighted_avoided_inr)} foot="weighted by 30-day failure probability" />
          </div>
          <div className="clickable-stat" onClick={() => openExpl('downtime_avoided')} style={{ cursor: 'pointer' }} title="Click for calculation details">
            <Stat label="Downtime avoided ⓘ" value={fmt.num(m.downtime_avoided_h)} unit="hours" foot="emergency vs planned outage" />
          </div>
        </div>

        {m.components.length === 0 ? (
          <div className="empty">No components currently flagged for replacement or urgent service.</div>
        ) : (
          <div className="scroll-x">
            <table>
              <thead>
                <tr>
                  <th>Component</th>
                  <th>Status</th>
                  <th className="num th-clickable" onClick={() => openExpl('failure_risk')} style={{ cursor: 'pointer' }} title="Click to view explanation">
                    Failure risk ⓘ
                  </th>
                  <th className="num th-clickable" onClick={() => openExpl('rul')} style={{ cursor: 'pointer' }} title="Click to view explanation">
                    RUL ⓘ
                  </th>
                  <th className="num th-clickable" onClick={() => openExpl('planned')} style={{ cursor: 'pointer' }} title="Click to view explanation">
                    Planned ⓘ
                  </th>
                  <th className="num th-clickable" onClick={() => openExpl('emergency')} style={{ cursor: 'pointer' }} title="Click to view explanation">
                    Emergency ⓘ
                  </th>
                  <th className="num th-clickable" onClick={() => openExpl('avoided')} style={{ cursor: 'pointer' }} title="Click to view explanation">
                    Avoided ⓘ
                  </th>
                </tr>
              </thead>
              <tbody>
                {m.components.map((r) => (
                  <tr key={r.component_id}>
                    <td><strong>{r.elevator}</strong> · {r.component}</td>
                    <td><StatusBadge status={r.status} /></td>
                    <td className="num">{fmt.pct(r.failure_probability)}</td>
                    <td className="num">{fmt.rul(r.rul_days)}</td>
                    <td className="num">{fmt.inr(r.planned_cost_inr)}</td>
                    <td className="num">{fmt.inr(r.emergency_cost_inr)}</td>
                    <td className="num" style={{ color: 'var(--good)', fontWeight: 600 }}>{fmt.inr(r.avoided_cost_inr)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <ul className="assumptions" style={{ marginTop: 14 }}>
          {m.assumptions.map((a) => <li key={a}>{a}</li>)}
        </ul>
      </div>

      {activeMetric && (
        <MetricModal
          metricKey={activeMetric}
          info={explanations[activeMetric]}
          onClose={() => setActiveMetric(null)}
        />
      )}
    </div>
  )
}

export default function Energy() {
  const [view, setView] = useStickyParam('view', 'month')
  const [elevator, setElevator] = useStickyParam('elevator', '')
  const { data: elevators } = useLiveFetch('/elevators')

  const currentElevator = (elevators || []).find((e) => (e.id || e._id) === elevator)
  const headingContext = currentElevator
    ? `${currentElevator.name} · Energy consumption and predictive maintenance economics.`
    : 'Energy consumption, tariff costs, and predictive-vs-reactive maintenance savings across the fleet.'

  return (
    <>
      <BackBar trail={[{ label: 'Elevators', to: '/elevators' }, { label: 'Cost & energy' }]} />
      <div className="page-head">
        <div>
          <h1>Cost &amp; energy</h1>
          <div className="sub">{headingContext}</div>
        </div>
      </div>

      <div className="row" style={{ marginBottom: 16, flexWrap: 'wrap', gap: 12, alignItems: 'center' }}>
        <Seg label="View" value={view} onChange={setView} options={VIEWS} />
        <ElevatorSelect value={elevator} onChange={setElevator} allLabel="All elevators" />
      </div>

      {view === 'trend'
        ? <TrendView range="month" elevator={elevator} />
        : <CalendarView view={view} elevator={elevator} />}
    </>
  )
}

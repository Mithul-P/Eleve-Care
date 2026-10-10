import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { useParams } from 'react-router-dom'
import BackBar, { useStickyParam } from '../components/BackBar'
import ElevatorSelect from '../components/ElevatorSelect'
import { ChartTooltip, ErrorBox, Loading, Seg, Stat, axisProps } from '../components/ui'
import { fmt } from '../format'
import { useLiveFetch } from '../live'

// Section 4 - usage cycle counts for the lift and for each component.
//
// Week / month / year toggle, lifetime usage against the rated cycle limit,
// a lift-vs-lift comparison and an exportable month-wise table.
//
// Consistency rule: every component's cycle count is DERIVED from the lift's
// own trips or door movements (see CYCLE_DEFINITION in the backend), so a
// component can never report more usage than its lift's duty allows. The
// "how calculated" tooltip on each card shows the exact formula.

const VIEWS = [
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
  { value: 'year', label: 'Year' },
]

const TYPE_LABEL = {
  rope: 'Suspension ropes',
  sheave: 'Sheave / pulley',
  bearing: 'Bearings',
  traction: 'Traction machine',
  door: 'Door system',
}

const barColor = (state) =>
  state === 'upcoming' ? 'var(--surface-3)' : state === 'running' ? 'var(--series-3)' : 'var(--series-1)'

/** Lifetime cycles used vs rated limit, as a progress bar. */
function LifetimeBar({ lt }) {
  const pct = Math.min(100, lt.used_pct)
  const color = lt.used_pct >= 95 ? 'var(--critical)' : lt.used_pct >= 80 ? 'var(--warning)' : 'var(--good)'
  return (
    <div className="lifetime">
      <div className="row small" style={{ justifyContent: 'space-between', marginBottom: 4 }}>
        <span className="secondary">Lifetime cycles used</span>
        <span className="mono" style={{ color }}>
          {fmt.num(lt.cycles_used)} / {fmt.num(lt.rated_cycles)} ({fmt.num(lt.used_pct, 1)}%)
        </span>
      </div>
      <div className="meter"><span style={{ width: `${Math.max(2, pct)}%`, background: color }} /></div>
      <div className="small muted" style={{ marginTop: 4 }}>
        {lt.cycles_based_remaining_days > 0
          ? <>Cycles-based remaining life: <strong>{fmt.rul(lt.cycles_based_remaining_days)}</strong> at {fmt.num(lt.avg_cycles_per_day)} cycles/day</>
          : <strong style={{ color: 'var(--critical)' }}>Beyond rated cycle life - replacement due</strong>}
      </div>
    </div>
  )
}

function ComponentCycleCard({ type, series, lifetime, view }) {
  const def = series.definition
  return (
    <div className="card">
      <div className="card-head">
        <h3>{TYPE_LABEL[type] || type}</h3>
        <span className="hint" title={`How calculated: ${def.formula}`}>
          {def.label} &#9432;
        </span>
        <div className="right mono">{fmt.num(series.total)}</div>
      </div>
      <ResponsiveContainer width="100%" height={150}>
        <BarChart data={series.buckets} margin={{ top: 4, right: 6, bottom: 0, left: -12 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="label" {...axisProps} interval={0} fontSize={10} />
          <YAxis {...axisProps} width={46} />
          <Tooltip cursor={{ fill: 'rgba(255,255,255,0.04)' }}
            content={<ChartTooltip
              labelFormatter={(l, p) => `${l} ${p?.[0]?.payload?.sub || ''}`}
              valueFormatter={(v) => `${fmt.num(v)} cycles`} />} />
          <Bar dataKey="cycles" name="cycles" radius={[3, 3, 0, 0]} isAnimationActive={false}>
            {series.buckets.map((b) => <Cell key={b.key} fill={barColor(b.state)} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <div className="small muted" style={{ margin: '6px 0 10px' }}>
        How calculated: {def.formula}
      </div>
      <LifetimeBar lt={lifetime} />
      <div className="small secondary" style={{ marginTop: 8 }}>
        {view === 'week' && 'Count for each day of the current week.'}
        {view === 'month' && `Count for each week of the current month. Month total ${fmt.num(series.total)}.`}
        {view === 'year' && `Count for each month of the current year. Year total ${fmt.num(series.total)}.`}
      </div>
    </div>
  )
}

function ComparisonBlock() {
  const { data } = useLiveFetch('/analytics/cycles/comparison')
  if (!data) return <Loading />
  const rows = data.rows
  return (
    <div className="grid g2" style={{ alignItems: 'start', marginBottom: 14 }}>
      <div className="card">
        <div className="card-head">
          <h3>Lift comparison</h3>
          <span className="hint">motor starts = one per trip</span>
        </div>
        <table>
          <thead><tr><th>Lift</th><th className="num">Week</th><th className="num">Month</th><th className="num">Year</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.elevator_id}>
                <td>{r.name}</td>
                <td className="num">{fmt.num(r.week)}</td>
                <td className="num">{fmt.num(r.month)}</td>
                <td className="num">{fmt.num(r.year)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="card">
        <div className="card-head"><h3>Year-to-date cycles by lift</h3><span className="hint">cycles</span></div>
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={rows} margin={{ top: 6, right: 8, bottom: 0, left: -4 }}>
            <CartesianGrid stroke="var(--grid)" vertical={false} />
            <XAxis dataKey="name" {...axisProps} interval={0} fontSize={10} angle={-20} textAnchor="end" height={54} />
            <YAxis {...axisProps} width={56} />
            <Tooltip cursor={{ fill: 'rgba(255,255,255,0.04)' }}
              content={<ChartTooltip valueFormatter={(v) => `${fmt.num(v)} cycles`} />} />
            <Bar dataKey="year" name="year to date" fill="var(--series-1)" radius={[4, 4, 0, 0]} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

function MonthlyTable({ elevatorId }) {
  const { data } = useLiveFetch('/analytics/cycles/monthly')
  if (!data) return <Loading />
  const rows = data.rows.filter((r) => !elevatorId || r.elevator_id === elevatorId)
  // Pivot to month columns: one row per lift + component.
  const months = [...new Set(rows.map((r) => r.month))]
  const keys = [...new Set(rows.map((r) => `${r.elevator}||${r.component_type}`))]
  const lookup = new Map(rows.map((r) => [`${r.elevator}||${r.component_type}||${r.month}`, r.cycles]))

  const downloadCsv = () => {
    // Build the CSV client-side from the data already on screen, so the export
    // is guaranteed to match what the user is looking at.
    const header = ['Lift', 'Component', ...months]
    const lines = [header.join(',')]
    keys.forEach((k) => {
      const [lift, type] = k.split('||')
      lines.push([lift, type, ...months.map((m) => lookup.get(`${lift}||${type}||${m}`) ?? 0)].join(','))
    })
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'elev-care-monthly-cycles.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="card">
      <div className="card-head">
        <h3>Month-wise count data</h3>
        <span className="hint">Jan-Dec, every lift and component</span>
        <div className="right"><button className="btn sm" onClick={downloadCsv}>Export CSV</button></div>
      </div>
      <div className="scroll-x">
        <table className="tnum">
          <thead>
            <tr><th>Lift</th><th>Component</th>{months.map((m) => <th key={m} className="num">{m}</th>)}</tr>
          </thead>
          <tbody>
            {keys.map((k) => {
              const [lift, type] = k.split('||')
              return (
                <tr key={k}>
                  <td>{lift}</td>
                  <td className="small secondary">{TYPE_LABEL[type] || type}</td>
                  {months.map((m) => {
                    const v = lookup.get(`${lift}||${type}||${m}`) ?? 0
                    return <td key={m} className="num">{v ? fmt.num(v) : '-'}</td>
                  })}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default function Cycles() {
  const { id } = useParams()
  const [view, setView] = useStickyParam('view', 'week')
  const { data: elevators } = useLiveFetch('/elevators')
  const [selected, setSelected] = useStickyParam('lift', id || '')
  const liftId = selected || id || (elevators?.[0]?.id ?? '')
  const { data, error } = useLiveFetch(liftId ? `/elevators/${liftId}/cycles?view=${view}` : null)

  // The trail's middle part (the elevator name) isn't known until it loads;
  // fall back to a plain "Usage cycles" crumb so the back button still shows.
  const loadingTrail = id
    ? [{ label: 'Elevators', to: '/elevators' }, { label: 'Usage cycles', to: `/elevators/${id}` }]
    : [{ label: 'Elevators', to: '/elevators' }, { label: 'Usage cycles' }]
  if (error && !data) return (
    <>
      <BackBar trail={loadingTrail} />
      <ErrorBox error={error} />
    </>
  )
  if (!liftId || !data) return (
    <>
      <BackBar trail={loadingTrail} />
      <Loading />
    </>
  )

  const trail = id
    ? [{ label: 'Elevators', to: '/elevators' }, { label: data.elevator_name, to: `/elevators/${id}` }, { label: 'Usage cycles' }]
    : [{ label: 'Elevators', to: '/elevators' }, { label: 'Usage cycles' }]

  const types = Object.keys(data.components)

  return (
    <>
      <BackBar trail={trail} />
      <div className="page-head">
        <div>
          <h1>Usage cycles</h1>
          <div className="sub">
            {data.elevator_name} - {data.period_label}. Component cycles are derived from the lift's own trips,
            so a component can never out-cycle its lift.
          </div>
        </div>
        <div className="right row" style={{ alignItems: 'center', gap: 10 }}>
          <Seg label="Period" value={view} onChange={setView} options={VIEWS} />
          {!id && (
            <ElevatorSelect
              value={liftId}
              onChange={(val) => setSelected(val)}
              elevators={elevators}
              includeAll={false}
            />
          )}
        </div>
      </div>

      <div className="grid g4 kpi-grid" style={{ marginBottom: 14 }}>
        <Stat label="Lift trips" value={fmt.num(data.lift_trips.total)} foot={`${data.period_label} · ${view}`} />
        <Stat label="Rope cycles" value={fmt.num(data.components.rope.total)}
          foot={`${fmt.num(data.lifetime.rope.used_pct, 1)}% of rated life used`}
          color={data.lifetime.rope.used_pct >= 95 ? 'var(--critical)' : undefined} />
        <Stat label="Door cycles" value={fmt.num(data.components.door.total)}
          foot={`${fmt.num(data.lifetime.door.used_pct, 1)}% of rated life used`} />
        <Stat label="Motor starts" value={fmt.num(data.components.traction.total)}
          foot={`${fmt.num(data.lifetime.traction.used_pct, 1)}% of rated life used`} />
      </div>

      {/* Elevator Age & Current Usage Summary (Section 11.A & 12) */}
      {data.age_and_usage && (
        <div className="card" style={{ marginBottom: 14 }}>
          <div className="card-head">
            <h3>Elevator Age &amp; Lifetime Usage</h3>
            <span className="hint">Commissioning record &amp; duty summary for {data.elevator_name}</span>
          </div>
          <div className="grid g4" style={{ marginBottom: 4 }}>
            <div className="stat">
              <div className="stat-label">Installation Date</div>
              <div className="stat-value" style={{ fontSize: 18 }}>{fmt.date(data.age_and_usage.installation_date)}</div>
              <div className="stat-foot">Commissioned</div>
            </div>
            <div className="stat">
              <div className="stat-label">Elevator Age</div>
              <div className="stat-value" style={{ fontSize: 18 }}>{data.age_and_usage.age_label}</div>
              <div className="stat-foot">{fmt.num(data.age_and_usage.age_days)} operational days</div>
            </div>
            <div className="stat">
              <div className="stat-label">Daily Average Duty</div>
              <div className="stat-value" style={{ fontSize: 18 }}>{fmt.num(data.age_and_usage.usage_summary?.daily_trips_avg)} <span className="unit">trips/day</span></div>
              <div className="stat-foot">{fmt.num(data.age_and_usage.usage_summary?.car_calls_per_trip, 2)} calls/trip</div>
            </div>
            <div className="stat">
              <div className="stat-label">Total Lifetime Trips</div>
              <div className="stat-value" style={{ fontSize: 18 }}>{fmt.num(data.age_and_usage.usage_summary?.total_trips)}</div>
              <div className="stat-foot">{fmt.num(data.age_and_usage.usage_summary?.total_rope_cycles)} rope cycles</div>
            </div>
          </div>
        </div>
      )}

      {/* Service & Maintenance History (Section 11.B) */}
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-head">
          <h3>Service &amp; Maintenance History</h3>
          <span className="hint">Previous service events for {data.elevator_name}</span>
        </div>
        {data.service_history && data.service_history.length > 0 ? (
          <div className="scroll-x">
            <table>
              <thead>
                <tr>
                  <th style={{ width: 140 }}>Date</th>
                  <th style={{ width: 130 }}>Action</th>
                  <th>Component</th>
                  <th style={{ width: 140 }}>Technician</th>
                  <th>Notes</th>
                </tr>
              </thead>
              <tbody>
                {data.service_history.map((s) => (
                  <tr key={s.id}>
                    <td className="small muted">{fmt.dateTime(s.date)}</td>
                    <td><span className="badge neutral">{s.action}</span></td>
                    <td className="small"><strong>{s.component}</strong></td>
                    <td className="small">{s.by}</td>
                    <td className="small secondary">{s.notes}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="small muted" style={{ padding: '8px 0' }}>No service history recorded for this elevator.</div>
        )}
      </div>

      <div className="grid g2" style={{ alignItems: 'start', marginBottom: 14 }}>
        {types.map((t) => (
          <ComponentCycleCard key={t} type={t} series={data.components[t]} lifetime={data.lifetime[t]} view={view} />
        ))}
      </div>

      <ComparisonBlock />
      <MonthlyTable elevatorId={id ? liftId : null} />

      <ul className="assumptions">
        <li>One cycle definition per component type, shown on each card: ropes and sheave count rope bends per trip, bearings and the traction machine count one load reversal or motor start per trip, and the door system counts open/close movements.</li>
        <li>Counts are tied to the real calendar: past days, weeks and months are frozen and never change; only the current period advances with the clock.</li>
        <li>Rated cycle limits are indicative design-life figures per component type, used to compute the remaining-life estimate.</li>
      </ul>
    </>
  )
}

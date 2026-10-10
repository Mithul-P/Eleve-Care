import { useState } from 'react'
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { ChartTooltip, ErrorBox, Loading, Seg, Stat, StatusBadge, axisProps } from '../components/ui'
import { fmt } from '../format'
import { useLiveFetch } from '../live'

const RANGES = [{ value: 'week', label: '7 days' }, { value: 'month', label: '30 days' }, { value: 'quarter', label: '90 days' }]

export default function Energy() {
  const [range, setRange] = useState('month')
  const [elevator, setElevator] = useState('')
  const { data: elevators } = useLiveFetch('/elevators')
  const path = elevator ? `/elevators/${elevator}/energy?range=${range}` : `/analytics/energy?range=${range}`
  const { data, error, loading } = useLiveFetch(path)
  if (error && !data) return <ErrorBox error={error} />
  const t = data?.totals
  const m = data?.maintenance
  return (
    <>
      <div className="page-head">
        <div><h1>Cost & energy</h1><div className="sub">Energy consumption and cost at ₹{data?.tariff_inr_per_kwh ?? 5}/unit, and what degraded components are costing you.</div></div>
      </div>
      <div className="row" style={{ marginBottom: 16 }}>
        <Seg label="Range" value={range} onChange={setRange} options={RANGES} />
        <select value={elevator} onChange={(e) => setElevator(e.target.value)} style={{ width: 'auto' }} aria-label="Elevator">
          <option value="">All elevators</option>
          {(elevators || []).map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
        </select>
      </div>
      {!data ? <Loading /> : (
        <div className={loading ? 'loading-frame' : ''}>
          <div className="grid g4" style={{ marginBottom: 14 }}>
            <Stat label="Energy consumed" value={fmt.num(t.energy_kwh)} unit="kWh" foot={`${fmt.num(t.avg_daily_kwh, 1)} kWh/day · ${fmt.num(t.kwh_per_trip * 1000, 0)} Wh/trip`} />
            <Stat label="Energy cost" value={fmt.inr(t.cost_inr)} foot={`${fmt.inr(t.avg_daily_cost_inr)} per day`} />
            <Stat label="Wasted by degradation" value={fmt.inr(t.excess_cost_inr)} foot={`${fmt.num(t.excess_kwh, 1)} kWh above healthy baseline`} color="var(--serious)" />
            <Stat label="Projected annual cost" value={fmt.inr(t.projected_annual_cost_inr)} foot={`${fmt.inr(t.projected_annual_excess_cost_inr)}/yr recoverable by maintenance`} />
          </div>

          <div className="card" style={{ marginBottom: 14 }}>
            <div className="card-head">
              <h3>Daily energy consumption</h3><span className="hint">kWh</span>
              <div className="right legend"><span><span className="k" style={{ background: 'var(--series-1)' }} />Healthy baseline</span><span><span className="k" style={{ background: 'var(--series-2)' }} />Excess from degradation</span></div>
            </div>
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={data.daily} margin={{ top: 6, right: 8, bottom: 0, left: -10 }} barCategoryGap={2}>
                <CartesianGrid stroke="var(--grid)" vertical={false} />
                <XAxis dataKey="date" {...axisProps} tickFormatter={fmt.dateShort} minTickGap={30} />
                <YAxis {...axisProps} width={48} />
                <Tooltip cursor={{ fill: 'rgba(255,255,255,0.04)' }} content={<ChartTooltip labelFormatter={(d, p) => `${fmt.date(d)} · ${fmt.inr(p?.[0]?.payload?.cost_inr)}`} valueFormatter={(v) => `${v.toFixed(1)} kWh`} />} />
                <Bar dataKey="baseline_kwh" stackId="e" name="healthy baseline" fill="var(--series-1)" stroke="var(--surface-1)" strokeWidth={1} isAnimationActive={false} />
                <Bar dataKey="excess_kwh" stackId="e" name="excess (degradation)" fill="var(--series-2)" stroke="var(--surface-1)" strokeWidth={1} radius={[4, 4, 0, 0]} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="grid g2" style={{ marginBottom: 14 }}>
            <div className="card">
              <div className="card-head"><h3>Daily energy cost</h3><span className="hint">₹ at ₹{data.tariff_inr_per_kwh}/kWh</span></div>
              <ResponsiveContainer width="100%" height={200}>
                <LineChart data={data.daily} margin={{ top: 6, right: 8, bottom: 0, left: -6 }}>
                  <CartesianGrid stroke="var(--grid)" vertical={false} />
                  <XAxis dataKey="date" {...axisProps} tickFormatter={fmt.dateShort} minTickGap={30} />
                  <YAxis {...axisProps} width={52} tickFormatter={(v) => `₹${v}`} />
                  <Tooltip content={<ChartTooltip labelFormatter={fmt.date} valueFormatter={(v) => fmt.inr(v)} />} />
                  <Line type="monotone" dataKey="cost_inr" name="energy cost" stroke="var(--series-1)" strokeWidth={2} dot={false} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
            <div className="card">
              <div className="card-head"><h3>By elevator</h3><span className="hint">sorted by excess consumption</span></div>
              <table>
                <thead><tr><th>Elevator</th><th className="num">kWh</th><th className="num">Cost</th><th className="num">Excess</th><th className="num">Wh/trip</th></tr></thead>
                <tbody>
                  {data.per_elevator.map((r) => (
                    <tr key={r.elevator_id}>
                      <td>{r.name}</td><td className="num">{fmt.num(r.energy_kwh)}</td><td className="num">{fmt.inr(r.cost_inr)}</td>
                      <td className="num" style={{ color: r.excess_pct > 8 ? 'var(--serious)' : undefined }}>{fmt.num(r.excess_pct, 1)}%</td>
                      <td className="num">{fmt.num(r.kwh_per_trip * 1000)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="card">
            <div className="card-head"><h3>Savings analysis - predictive vs reactive maintenance</h3></div>
            <div className="grid g3" style={{ marginBottom: 14 }}>
              <Stat label="Avoided repair cost (flagged parts)" value={fmt.inr(m.total_avoided_inr)} foot="planned replacement instead of in-service failure" />
              <Stat label="Risk-weighted saving" value={fmt.inr(m.risk_weighted_avoided_inr)} foot="weighted by 30-day failure probability" />
              <Stat label="Downtime avoided" value={fmt.num(m.downtime_avoided_h)} unit="hours" foot="emergency vs planned outage" />
            </div>
            {m.components.length === 0 ? <div className="empty">No components currently flagged.</div> : (
              <table>
                <thead><tr><th>Component</th><th>Status</th><th className="num">Failure risk</th><th className="num">RUL</th><th className="num">Planned</th><th className="num">Emergency</th><th className="num">Avoided</th></tr></thead>
                <tbody>
                  {m.components.map((r) => (
                    <tr key={r.component_id}>
                      <td>{r.elevator} - {r.component}</td><td><StatusBadge status={r.status} /></td>
                      <td className="num">{fmt.pct(r.failure_probability)}</td><td className="num">{fmt.rul(r.rul_days)}</td>
                      <td className="num">{fmt.inr(r.planned_cost_inr)}</td><td className="num">{fmt.inr(r.emergency_cost_inr)}</td><td className="num">{fmt.inr(r.avoided_cost_inr)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <ul className="assumptions">{[...data.assumptions, ...m.assumptions].map((a) => <li key={a}>{a}</li>)}</ul>
          </div>
        </div>
      )}
    </>
  )
}

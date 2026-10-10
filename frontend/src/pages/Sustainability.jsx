import { useState } from 'react'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { ChartTooltip, ErrorBox, Loading, Seg, Stat, axisProps } from '../components/ui'
import { TYPE_LABEL, fmt } from '../format'
import { useLiveFetch } from '../live'

export default function Sustainability() {
  const [range, setRange] = useState('month')
  const { data, error, loading } = useLiveFetch(`/analytics/sustainability?range=${range}`)
  if (error && !data) return <ErrorBox error={error} />
  const o = data?.operational
  const le = data?.life_extension
  return (
    <>
      <div className="page-head">
        <div><h1>Sustainability</h1><div className="sub">Carbon footprint of operation, life extension from condition-based maintenance, and circular-economy levers.</div></div>
      </div>
      <div className="row" style={{ marginBottom: 16 }}>
        <Seg label="Range" value={range} onChange={setRange} options={[{ value: 'week', label: '7 days' }, { value: 'month', label: '30 days' }, { value: 'quarter', label: '90 days' }]} />
      </div>
      {!data ? <Loading /> : (
        <div className={loading ? 'loading-frame' : ''}>
          <div className="grid g4" style={{ marginBottom: 14 }}>
            <Stat label="Operational CO₂ (period)" value={fmt.num(o.co2_kg)} unit="kg" foot={`${fmt.num(o.energy_kwh)} kWh × ${data.grid_emission_kg_per_kwh} kg/kWh`} />
            <Stat label="CO₂ from degradation" value={fmt.num(o.excess_co2_kg, 1)} unit="kg" foot={`${fmt.num(o.projected_annual_excess_co2_kg)} kg/yr avoidable`} color="var(--serious)" />
            <Stat label="Projected annual CO₂" value={fmt.num(o.projected_annual_co2_kg / 1000, 1)} unit="t" foot="fleet operation" />
            <Stat label="Embodied CO₂ avoided" value={fmt.num(le.avoided_embodied_co2_kg_per_year)} unit="kg/yr" foot={`${fmt.num(le.avoided_replacements_per_year, 2)} fewer part replacements per year`} color="var(--good)" />
          </div>

          <div className="card" style={{ marginBottom: 14 }}>
            <div className="card-head"><h3>Daily operational emissions</h3><span className="hint">kg CO₂</span></div>
            <ResponsiveContainer width="100%" height={220}>
              <AreaChart data={o.daily} margin={{ top: 6, right: 8, bottom: 0, left: -10 }}>
                <CartesianGrid stroke="var(--grid)" vertical={false} />
                <XAxis dataKey="date" {...axisProps} tickFormatter={fmt.dateShort} minTickGap={30} />
                <YAxis {...axisProps} width={48} />
                <Tooltip content={<ChartTooltip labelFormatter={fmt.date} valueFormatter={(v) => `${v.toFixed(1)} kg`} />} />
                <Area type="monotone" dataKey="co2_kg" name="CO₂" stroke="var(--series-3)" fill="var(--series-3)" fillOpacity={0.18} strokeWidth={2} isAnimationActive={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>

          <div className="grid g2" style={{ marginBottom: 14, alignItems: 'start' }}>
            <div className="card">
              <div className="card-head"><h3>Component life extension</h3><span className="hint">time-based vs condition-based replacement</span></div>
              <table>
                <thead><tr><th>Component</th><th className="num">Units</th><th className="num">Typical life</th><th className="num">Life gain</th><th className="num">CO₂/part</th><th className="num">Avoided/yr</th></tr></thead>
                <tbody>
                  {le.components.map((r) => (
                    <tr key={r.type}>
                      <td>{TYPE_LABEL[r.type]}<div className="small muted">{r.material}</div></td>
                      <td className="num">{r.units}</td><td className="num">{r.typical_life_years} yr</td>
                      <td className="num">+{r.life_extension_pct}%</td><td className="num">{fmt.num(r.embodied_co2_per_part_kg)} kg</td>
                      <td className="num">{fmt.num(r.avoided_co2_kg_per_year)} kg</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="card">
              <div className="card-head"><h3>Circular economy notes</h3></div>
              {data.circular_economy.map((n) => (
                <div className="factor" key={n.title}><strong>{n.title}</strong><div className="factor-advice">{n.text}</div></div>
              ))}
            </div>
          </div>
          <div className="card"><h3>Assumptions</h3><ul className="assumptions">{data.assumptions.map((a) => <li key={a}>{a}</li>)}</ul></div>
        </div>
      )}
    </>
  )
}

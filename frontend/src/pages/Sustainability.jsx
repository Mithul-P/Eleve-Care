import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { ChartTooltip, ErrorBox, Loading, Seg, Stat, axisProps } from '../components/ui'
import { TYPE_LABEL, fmt } from '../format'
import { useLiveFetch } from '../live'
import BackBar, { useStickyParam } from '../components/BackBar'
import ElevatorSelect from '../components/ElevatorSelect'

const RANGES = [
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
  { value: 'year', label: 'Year' },
]

const PERIOD_TITLE = { week: 'Weekly operational emissions', month: 'Monthly operational emissions', year: 'Yearly operational emissions' }

export default function Sustainability() {
  const [range, setRange] = useStickyParam('range', 'month')
  const [elevator, setElevator] = useStickyParam('elevator', '')
  const path = `/analytics/sustainability?range=${range}${elevator ? `&elevator_id=${elevator}` : ''}`
  const { data, error, loading } = useLiveFetch(path)

  if (error && !data) return (
    <>
      <BackBar trail={[{ label: 'Elevators', to: '/elevators' }, { label: 'Sustainability' }]} />
      <ErrorBox error={error} />
    </>
  )

  const o = data?.operational
  const le = data?.life_extension
  const titleContext = data?.elevator_name
    ? `${data.elevator_name} · Carbon footprint & life extension`
    : 'Carbon footprint of operation & life extension from condition-based maintenance across the fleet.'

  return (
    <>
      <BackBar trail={[{ label: 'Elevators', to: '/elevators' }, { label: 'Sustainability' }]} />
      <div className="page-head">
        <div>
          <h1>Sustainability</h1>
          <div className="sub">{titleContext}</div>
        </div>
      </div>

      <div className="row" style={{ marginBottom: 16, flexWrap: 'wrap', gap: 12, alignItems: 'center' }}>
        <Seg label="Range" value={range} onChange={setRange} options={RANGES} />
        <ElevatorSelect value={elevator} onChange={setElevator} allLabel="All elevators (fleet-wide)" />
      </div>

      {!data ? <Loading /> : (
        <div className={loading ? 'loading-frame' : ''}>
          <div className="grid g4" style={{ marginBottom: 14 }}>
            <Stat label="Operational CO₂ (period)" value={fmt.num(o.co2_kg)} unit="kg" foot={`${fmt.num(o.energy_kwh)} kWh × ${data.grid_emission_kg_per_kwh} kg/kWh`} />
            <Stat label="CO₂ from degradation" value={fmt.num(o.excess_co2_kg, 1)} unit="kg" foot={`${fmt.num(o.projected_annual_excess_co2_kg)} kg/yr avoidable`} color="var(--serious)" />
            <Stat label="Projected annual CO₂" value={fmt.num(o.projected_annual_co2_kg / 1000, 1)} unit="t" foot={data.elevator_name ? 'elevator operation' : 'fleet operation'} />
            <Stat label="Embodied CO₂ avoided" value={fmt.num(le.avoided_embodied_co2_kg_per_year)} unit="kg/yr" foot={`${fmt.num(le.avoided_replacements_per_year, 2)} fewer part replacements per year`} color="var(--good)" />
          </div>

          <div className="card" style={{ marginBottom: 14 }}>
            <div className="card-head">
              <h3>{PERIOD_TITLE[range] || 'Operational emissions'}</h3>
              <span className="hint">{data.elevator_name ? `${data.elevator_name} · ` : ''}kg CO₂ · {o.period_label}</span>
            </div>
            <ResponsiveContainer width="100%" height={220}>
              <AreaChart data={o.periods} margin={{ top: 6, right: 8, bottom: 0, left: -10 }}>
                <CartesianGrid stroke="var(--grid)" vertical={false} />
                <XAxis dataKey="label" {...axisProps} interval={0} angle={-30} textAnchor="end" height={52} />
                <YAxis {...axisProps} width={48} />
                <Tooltip content={<ChartTooltip valueFormatter={(v) => `${v.toFixed(1)} kg`} />} />
                <Area type="monotone" dataKey="co2_kg" name="CO₂" stroke="var(--series-3)" fill="var(--series-3)" fillOpacity={0.18} strokeWidth={2} isAnimationActive={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>

          <div className="card" style={{ marginBottom: 14 }}>
            <div className="card-head">
              <h3>Component life extension</h3>
              <span className="hint">time-based vs condition-based replacement{data.elevator_name ? ` · ${data.elevator_name}` : ''}</span>
            </div>
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

          <div className="card"><h3>Assumptions</h3><ul className="assumptions">{data.assumptions.map((a) => <li key={a}>{a}</li>)}</ul></div>
        </div>
      )}
    </>
  )
}

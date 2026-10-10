import { useState } from 'react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { ChartTooltip, ErrorBox, Loading, Seg, axisProps } from '../components/ui'
import { TYPE_LABEL, fmt } from '../format'
import { useLiveFetch } from '../live'

const GENERIC_LABELS = {
  tension_imbalance_pct: 'Rope tension imbalance', tension_deviation_pct: 'Mean tension deviation',
  vib_rms: 'Vibration RMS', vib_peak: 'Vibration peak', vib_kurtosis: 'Vibration kurtosis', vib_crest: 'Vibration crest factor',
  fault_band_ratio: 'Defect-band energy', motor_current_ratio: 'Motor current', temperature_rise: 'Temperature rise',
  fatigue_index: 'Fatigue life consumed', rms_trend: 'Vibration growth rate', imbalance_trend: 'Imbalance growth rate',
}

export default function ModelCard() {
  const { data, error } = useLiveFetch('/analytics/model')
  const [type, setType] = useState('rope')
  if (error && !data) return <ErrorBox error={error} />
  if (!data) return <Loading />
  const labels = { ...GENERIC_LABELS, ...Object.fromEntries(Object.entries(data.thresholds[type]).map(([k, v]) => [k, v.label])) }
  const imp = Object.entries(data.importances[type]).map(([k, v]) => ({ name: labels[k] || k, v: v * 100 })).sort((a, b) => b.v - a.v).slice(0, 8)
  return (
    <>
      <div className="page-head">
        <div><h1>Model card</h1><div className="sub">{data.algorithm} · version <span className="mono">{data.version}</span> · trained {fmt.dateTime(data.trained_at)}</div></div>
      </div>
      <div className="grid g2" style={{ marginBottom: 14, alignItems: 'start' }}>
        <div className="card">
          <h3 style={{ marginBottom: 8 }}>How a prediction is made</h3>
          <ol className="secondary" style={{ paddingLeft: 18, margin: 0, lineHeight: 1.6 }}>
            <li><strong>Signal processing</strong> - each accelerometer waveform → RMS, peak, crest factor, kurtosis and FFT energy in the component's defect band.</li>
            <li><strong>Physics features</strong> - rope tension imbalance, tension drift, motor current vs rated, temperature rise, and a Palmgren–Miner fatigue index (trips × load<sup>m</sup>, Basquin).</li>
            <li><strong>Window</strong> - 24 h medians + 7-day trends per component.</li>
            <li><strong>ML</strong> - gradient-boosted trees with monotonic constraints (risk can only rise as an indicator worsens) give P(failure in {data.horizon_days} days) and RUL.</li>
            <li><strong>Blend</strong> - health = 100 × (1 − ½·P<sub>fail</sub> − ½·physics severity); explanation ranks factors by severity × importance.</li>
          </ol>
          <p className="note">Trained on simulated run-to-failure trajectories from the same physics simulator; evaluated on held-out units. Replace with field data as it accrues.</p>
        </div>
        <div className="card">
          <h3 style={{ marginBottom: 8 }}>Held-out performance</h3>
          <table>
            <thead><tr><th>Component</th><th className="num">ROC AUC</th><th className="num">Brier</th><th className="num">RUL MAE ≤90 d</th><th className="num">RUL MAE ≤1 yr</th><th className="num">Units</th></tr></thead>
            <tbody>
              {Object.entries(data.metrics).map(([k, m]) => (
                <tr key={k}><td>{TYPE_LABEL[k]}</td><td className="num">{fmt.num(m.roc_auc, 3)}</td><td className="num">{fmt.num(m.brier, 3)}</td>
                  <td className="num">{fmt.num(m.rul_mae_days_within_90d, 1)} d</td><td className="num">{fmt.num(m.rul_mae_days_within_1y, 1)} d</td><td className="num">{m.units}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="card">
        <div className="card-head"><h3>Feature importance</h3><span className="hint">permutation importance on held-out units, % of total</span>
          <div className="right"><Seg label="Component" value={type} onChange={setType} options={Object.keys(TYPE_LABEL).map((k) => ({ value: k, label: TYPE_LABEL[k] }))} /></div></div>
        <ResponsiveContainer width="100%" height={Math.max(200, imp.length * 34)}>
          <BarChart data={imp} layout="vertical" margin={{ top: 0, right: 30, bottom: 0, left: 10 }}>
            <CartesianGrid stroke="var(--grid)" horizontal={false} />
            <XAxis type="number" {...axisProps} unit="%" />
            <YAxis type="category" dataKey="name" {...axisProps} width={200} />
            <Tooltip cursor={{ fill: 'rgba(255,255,255,0.04)' }} content={<ChartTooltip valueFormatter={(v) => `${v.toFixed(1)}%`} />} />
            <Bar dataKey="v" name="importance" fill="var(--series-1)" radius={[0, 4, 4, 0]} maxBarSize={20} isAnimationActive={false}
              label={{ position: 'right', fill: 'var(--text-secondary)', fontSize: 11, formatter: (v) => `${v.toFixed(0)}%` }} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </>
  )
}

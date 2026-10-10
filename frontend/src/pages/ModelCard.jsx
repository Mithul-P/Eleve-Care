import { useState } from 'react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { ChartTooltip, ErrorBox, Loading, Seg, axisProps } from '../components/ui'
import { TYPE_LABEL, fmt } from '../format'
import { useLiveFetch } from '../live'
import BackBar from '../components/BackBar'

const GENERIC_LABELS = {
  tension_imbalance_pct: 'Rope tension imbalance', tension_deviation_pct: 'Mean tension deviation',
  vib_rms: 'Vibration RMS', vib_peak: 'Vibration peak', vib_kurtosis: 'Vibration kurtosis', vib_crest: 'Vibration crest factor',
  fault_band_ratio: 'Defect-band energy', motor_current_ratio: 'Motor current', temperature_rise: 'Temperature rise',
  fatigue_index: 'Fatigue life consumed', rms_trend: 'Vibration growth rate', imbalance_trend: 'Imbalance growth rate',
}

function LawDetailModal({ law, onClose }) {
  if (!law) return null
  return (
    <div className="modal-backdrop" onClick={onClose} style={{
      position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
      background: 'rgba(0, 0, 0, 0.75)', backdropFilter: 'blur(4px)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000, padding: 16
    }}>
      <div className="modal-content card" onClick={(e) => e.stopPropagation()} style={{
        maxWidth: 640, width: '100%', maxHeight: '90vh', overflowY: 'auto'
      }}>
        <div className="card-head" style={{ borderBottom: '1px solid var(--border)', paddingBottom: 10 }}>
          <div>
            <h3>{law.name}</h3>
            <span className="hint">{law.label} · {law.formula}</span>
          </div>
          <div className="right">
            <button className="btn ghost sm" onClick={onClose}>✕</button>
          </div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginTop: 14, fontSize: 13 }}>
          {law.formula && (
            <div>
              <strong className="small muted">GOVERNING FORMULA</strong>
              <pre className="code-block" style={{ margin: '6px 0 0', padding: '8px 12px' }}>{law.formula}</pre>
            </div>
          )}
          <div>
            <strong className="small muted">1. WHAT IT IS</strong>
            <div style={{ marginTop: 3, lineHeight: 1.5 }}>{law.what_it_is || law.note}</div>
          </div>
          <div>
            <strong className="small muted">2. WHAT IT MEASURES</strong>
            <div style={{ marginTop: 3, lineHeight: 1.5 }}>{law.what_it_measures || law.label}</div>
          </div>
          <div>
            <strong className="small muted">3. WHERE IT EXISTS IN ELEV CARE</strong>
            <div style={{ marginTop: 3, lineHeight: 1.5 }} className="secondary">{law.where_it_exists || 'Physics engine & simulator'}</div>
          </div>
          <div>
            <strong className="small muted">4. COMPONENT USING IT</strong>
            <div style={{ marginTop: 3, lineHeight: 1.5 }} className="secondary">{law.component_using_it || law.name}</div>
          </div>
          <div>
            <strong className="small muted">5. PROJECT DATA FEEDING IT</strong>
            <div style={{ marginTop: 3, lineHeight: 1.5 }}>{law.project_data_feeding_it || 'Sensor telemetry and trip records'}</div>
          </div>
          <div>
            <strong className="small muted">6. CONTRIBUTION TO PREDICTION</strong>
            <div style={{ marginTop: 3, lineHeight: 1.5 }}>{law.contribution_to_prediction || 'Direct feature in monotonic gradient boosted trees and physics health blend'}</div>
          </div>
        </div>

        <div style={{ marginTop: 20, textAlign: 'right' }}>
          <button className="btn primary sm" onClick={onClose}>Done</button>
        </div>
      </div>
    </div>
  )
}

export default function ModelCard() {
  const { data, error } = useLiveFetch('/analytics/model')
  const [type, setType] = useState('rope')
  const [selectedLaw, setSelectedLaw] = useState(null)
  if (error && !data) return (
    <>
      <BackBar trail={[{ label: 'Elevators', to: '/elevators' }, { label: 'Model card' }]} />
      <ErrorBox error={error} />
    </>
  )
  if (!data) return (
    <>
      <BackBar trail={[{ label: 'Elevators', to: '/elevators' }, { label: 'Model card' }]} />
      <Loading />
    </>
  )
  const labels = { ...GENERIC_LABELS, ...Object.fromEntries(Object.entries(data.thresholds[type]).map(([k, v]) => [k, v.label])) }
  const imp = Object.entries(data.importances[type]).map(([k, v]) => ({ name: labels[k] || k, v: v * 100 })).sort((a, b) => b.v - a.v).slice(0, 8)
  return (
    <>
      <BackBar trail={[{ label: 'Elevators', to: '/elevators' }, { label: 'Model card' }]} />
      <div className="page-head">
        <div>
          <h1>Model & physics card</h1>
          <div className="sub">{data.algorithm} · version <span className="mono">{data.version}</span> · trained {fmt.dateTime(data.trained_at)}</div>
        </div>
      </div>

      {/* How prediction is made + Held-out performance */}
      <div className="grid g2" style={{ marginBottom: 16, alignItems: 'stretch' }}>
        <div className="card">
          <div className="card-head">
            <h3>How prediction is made</h3>
            <span className="hint">signal processing → physics → ML → rules</span>
          </div>
          <ol className="secondary" style={{ paddingLeft: 18, margin: 0, lineHeight: 1.65 }}>
            <li><strong>Signal processing</strong> - each accelerometer waveform → RMS, peak, crest factor, kurtosis and FFT energy in the component's defect band.</li>
            <li><strong>Physics features</strong> - rope tension imbalance, tension drift, motor current vs rated, temperature rise, and a life fraction from each component's own law (table below).</li>
            <li><strong>Window</strong> - 24 h medians + 7-day trends per component.</li>
            <li><strong>ML</strong> - gradient-boosted trees with monotonic constraints (risk can only rise as an indicator worsens) give P(failure in {data.horizon_days} days) and RUL.</li>
            <li><strong>Blend</strong> - health = 100 × (1 − ½·P<sub>fail</sub> − ½·physics severity); explanation ranks factors by severity × importance.</li>
            <li><strong>Rules</strong> - an engineering rule layer escalates on crossed limits, consumed life and growth trends, and holds unconfirmed or flapping verdicts (catalogue below).</li>
          </ol>
          <p className="note" style={{ marginTop: 12 }}>Trained on simulated run-to-failure trajectories from the same physics simulator; evaluated on held-out units. Replace with field data as it accrues.</p>
        </div>

        <div className="card">
          <div className="card-head">
            <h3>Held-out performance</h3>
            <span className="hint">evaluated across test fleet</span>
          </div>
          <table>
            <thead>
              <tr>
                <th>Component</th>
                <th className="num">ROC AUC</th>
                <th className="num">Brier</th>
                <th className="num">RUL MAE Week (≤7 d)</th>
                <th className="num">RUL MAE Month (≤30 d)</th>
                <th className="num">RUL MAE ≤1 yr</th>
                <th className="num">Units</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(data.metrics).map(([k, m]) => (
                <tr key={k}>
                  <td><strong>{TYPE_LABEL[k]}</strong></td>
                  <td className="num">{fmt.num(m.roc_auc, 3)}</td>
                  <td className="num">{fmt.num(m.brier, 3)}</td>
                  <td className="num">{m.rul_mae_days_within_7d != null ? `${fmt.num(m.rul_mae_days_within_7d, 1)} d` : `${fmt.num(m.rul_mae_days_within_90d, 1)} d`}</td>
                  <td className="num">{m.rul_mae_days_within_30d != null ? `${fmt.num(m.rul_mae_days_within_30d, 1)} d` : `${fmt.num(m.rul_mae_days_within_90d, 1)} d`}</td>
                  <td className="num">{fmt.num(m.rul_mae_days_within_1y, 1)} d</td>
                  <td className="num">{m.units}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* 4 & 5: Degradation laws + Physics inputs from material specifications */}
      {data.physics_models && (
        <div className="grid g2" style={{ marginBottom: 16, alignItems: 'stretch' }}>
          <div className="card">
            <div className="card-head">
              <h3>Degradation laws</h3>
              <span className="hint">one approved model per component · click row for detailed explanation</span>
            </div>
            <table>
              <thead><tr><th>Component</th><th>Law</th><th>Formula</th><th>Details</th></tr></thead>
              <tbody>
                {Object.entries(data.physics_models).map(([k, m]) => (
                  <tr key={k} className="clickable" onClick={() => setSelectedLaw({ ...m, componentKey: k })} title="Click to view detailed explanation">
                    <td><strong>{TYPE_LABEL[k]}</strong></td>
                    <td>{m.name}</td>
                    <td className="mono small">{m.formula}</td>
                    <td><button className="btn ghost sm" onClick={(e) => { e.stopPropagation(); setSelectedLaw({ ...m, componentKey: k }) }}>Explain &#9432;</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="card">
            <div className="card-head">
              <h3>Physics inputs from material specifications</h3>
              <span className="hint">per component: user spec or default</span>
            </div>
            <div className="small muted" style={{ marginBottom: 8 }}>
              Defaults: S_u {data.physics_defaults?.rope_breaking_load_kn} kN · D/d {data.physics_defaults?.d_over_d} · H {data.physics_defaults?.sheave_hardness_pa?.toExponential(2)} Pa · C {data.physics_defaults?.bearing_c_kn} kN · rated rise {data.physics_defaults?.rated_temp_rise_c} °C
            </div>
            <div style={{ maxHeight: 280, overflow: 'auto' }}>
              <table>
                <thead><tr><th>Component</th><th>Input</th><th>Value</th><th>Source</th></tr></thead>
                <tbody>
                  {(data.physics_inputs || []).flatMap((p) => p.inputs.map((i) => (
                    <tr key={`${p.component_id}-${i.key}`}>
                      <td className="small">{p.elevator_id} · {p.name}{p.material?.label ? ` — ${p.material.label}` : ''}</td>
                      <td className="small">{i.label}</td>
                      <td className="mono small">{typeof i.value === 'number' ? (i.value > 1e5 ? i.value.toExponential(3) : i.value) : i.value} {i.unit}</td>
                      <td><span className={`badge sm ${i.source === 'user' ? 'good' : 'neutral'}`}>{i.source === 'user' ? 'user spec' : 'default'}</span></td>
                    </tr>
                  )))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* 6: Rule layer */}
      {data.rules && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-head">
            <h3>Rule layer</h3>
            <span className="hint">applied after the model, every prediction</span>
          </div>
          <div className="grid g3">
            {(data.rules || []).map((r) => (
              <div className="factor" key={r.id} style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', padding: '12px', background: 'var(--surface-2)' }}>
                <div className="factor-top" style={{ marginBottom: 6 }}>
                  <strong style={{ color: r.family === 'escalation' ? 'var(--critical)' : 'var(--warning)' }}>{r.id}</strong>
                  <span style={{ fontWeight: 600 }}>{r.name}</span>
                  <span className="val small muted">{r.family}</span>
                </div>
                <div className="factor-advice" style={{ lineHeight: 1.5 }}>{r.text}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 7: Feature importance */}
      <div className="card">
        <div className="card-head">
          <h3>Feature importance</h3>
          <span className="hint">permutation importance on held-out units, % of total</span>
          <div className="right">
            <Seg label="Component" value={type} onChange={setType} options={Object.keys(TYPE_LABEL).map((k) => ({ value: k, label: TYPE_LABEL[k] }))} />
          </div>
        </div>
        <ResponsiveContainer width="100%" height={Math.max(220, imp.length * 36)}>
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

      {selectedLaw && <LawDetailModal law={selectedLaw} onClose={() => setSelectedLaw(null)} />}
    </>
  )
}

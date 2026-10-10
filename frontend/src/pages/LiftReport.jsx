// Section 3 - LIFT REPORT. Suspension rope section first; printable (print CSS) and
// exportable to PDF via the browser's print dialog. Everything comes from
// GET /elevators/:id/reports/suspension-rope; the chatbot gets the same data.
import { Link, useParams } from 'react-router-dom'
import { Bar, BarChart, CartesianGrid, Cell, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import BackBar from '../components/BackBar'
import { ChartTooltip, Loading, axisProps } from '../components/ui'
import { fmt } from '../format'
import { IconAlert, IconCheck, IconChat } from '../icons'
import { useLiveFetch } from '../live'

const STATUS = { healthy: ['Healthy', 'good'], watch: ['Watch', 'medium'], faulty: ['Faulty', 'at-risk'] }

function Spark({ values, highlight }) {
  if (!values?.length) return null
  const w = 160, h = 36, min = Math.min(...values), max = Math.max(...values), span = max - min || 1
  const pts = values.map((v, i) => `${(i / Math.max(1, values.length - 1)) * w},${h - 2 - ((v - min) / span) * (h - 4)}`).join(' ')
  return <svg width={w} height={h} aria-hidden="true"><polyline points={pts} fill="none" stroke={highlight ? 'var(--critical)' : 'var(--series-1)'} strokeWidth="1.6" /></svg>
}

export default function LiftReport() {
  const { id } = useParams()
  const { data: r, error } = useLiveFetch(`/elevators/${id}/reports/suspension-rope`)
  if (error) return <div className="error">{error.message}</div>
  if (!r) return <Loading what="Building report" />
  const e = r.elevator
  const ask = () => window.dispatchEvent(new CustomEvent('elevcare:ask', { detail: { question: r.assistant_question, elevator_id: id } }))
  if (!r.available) {
    return <div><BackBar trail={[{ label: 'Elevators', to: '/elevators' }, { label: e.name, to: `/elevators/${id}` }, { label: 'Lift report' }]} /><div className="empty">Rope report not available yet: {r.reason}.</div></div>
  }
  const fault = r.verdict === 'fault'
  const faulty = new Set(r.faulty_ropes)
  const bars = r.ropes.map((x) => ({ id: x.id, tension: x.median_tension_kn, status: x.status }))
  const avg = r.set_average_kn, lim = r.limits.fault_pct / 100, watch = r.limits.watch_pct / 100
  const days = r.series.map((s) => s.day)
  return (
    <div className="report">
      <div className="no-print"><BackBar trail={[{ label: 'Elevators', to: '/elevators' }, { label: e.name, to: `/elevators/${id}` }, { label: 'Lift report' }]} /></div>
      <div className="page-head">
        <div><h1>Lift report - {e.name}</h1><div className="sub">{e.building} · <span className="lift-type">Traction lift</span> · generated {fmt.dateTime(r.generated_at)} · data {fmt.date(r.data_from)} – {fmt.date(r.data_to)} ({r.readings} readings){r.material?.label ? <> · {r.material.label}</> : null}</div></div>
        <div className="actions no-print">
          <button className="btn" onClick={() => window.print()}>Print / Save as PDF</button>
          <button className="btn primary" onClick={ask}><IconChat />Ask the assistant: "{r.assistant_question}"</button>
        </div>
      </div>

      <section className="report-section">
        <h2>1. Suspension ropes</h2>
        {/* a) banner */}
        <div className={`report-banner ${fault ? 'fault' : 'ok'}`} role="status">
          {fault ? <IconAlert /> : <IconCheck />}
          <strong>{r.banner}</strong>
          <span className="small">{fault ? `${r.faulty_ropes.join(', ')} above the ${r.limits.fault_pct}% deviation limit` : `all ropes within ±${r.limits.fault_pct}% of the set average`}</span>
        </div>

        {/* b) per-rope table + bar chart */}
        <div className="grid g2">
          <div className="card">
            <div className="card-head"><h3>Per-rope tension</h3><span className="hint">24 h median · deviation from set average {avg.toFixed(2)} kN · trend of that deviation{r.material?.label ? ` · ${r.material.label}` : ''}</span></div>
            <table>
              <thead><tr><th>Rope</th><th>Tension</th><th>Deviation</th><th>Trend 7 d</th><th>Trend 30 d</th><th>Status</th></tr></thead>
              <tbody>{r.ropes.map((x) => {
                const [label, cls] = STATUS[x.status]
                return (
                  <tr key={x.id} className={faulty.has(x.id) ? 'row-fault' : ''}>
                    <td><strong>{x.id}</strong>{faulty.has(x.id) && <span className="replace-tag risk-critical">FAULTY</span>}</td>
                    <td className="mono">{x.median_tension_kn.toFixed(2)} kN</td>
                    <td className="mono" style={{ color: x.deviation_pct >= r.limits.fault_pct ? 'var(--critical)' : x.deviation_pct >= r.limits.watch_pct ? 'var(--warning)' : undefined }}>{x.deviation_pct >= 0 ? '+' : ''}{x.deviation_pct.toFixed(1)} %</td>
                    <td className="mono">{x.trend_7d_pct_per_day >= 0 ? '+' : ''}{x.trend_7d_pct_per_day.toFixed(2)} %/d</td>
                    <td className="mono">{x.trend_30d_pct_per_day >= 0 ? '+' : ''}{x.trend_30d_pct_per_day.toFixed(2)} %/d</td>
                    <td><span className={`badge ${cls}`}>{x.status === 'healthy' ? <IconCheck /> : <IconAlert />}{label}</span></td>
                  </tr>)
              })}</tbody>
            </table>
            <div className="small muted" style={{ marginTop: 6 }}>Healthy below {r.limits.watch_pct}% · Watch {r.limits.watch_pct}–{r.limits.fault_pct}% or trending up · Faulty ≥ {r.limits.fault_pct}% or over the limit for the last {r.limits.persistence_readings} readings (limits: physics.THRESHOLDS).</div>
          </div>
          <div className="card">
            <div className="card-head"><h3>Tension per rope</h3><span className="hint">dashed = set average · shaded = alarm band (±{r.limits.fault_pct}%)</span></div>
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={bars} margin={{ top: 8, right: 12, left: -10, bottom: 0 }}>
                <CartesianGrid stroke="var(--grid)" strokeDasharray="2 4" />
                <XAxis dataKey="id" {...axisProps} /><YAxis {...axisProps} domain={[avg * 0.7, avg * 1.3]} unit=" kN" />
                <Tooltip content={<ChartTooltip valueFormatter={(v) => `${v.toFixed(2)} kN`} />} />
                <ReferenceArea y1={avg * (1 - watch)} y2={avg * (1 + watch)} fill="var(--good)" fillOpacity={0.08} />
                <ReferenceArea y1={avg * (1 + watch)} y2={avg * (1 + lim)} fill="var(--warning)" fillOpacity={0.12} />
                <ReferenceArea y1={avg * (1 + lim)} y2={avg * 1.3} fill="var(--critical)" fillOpacity={0.12} />
                <ReferenceLine y={avg} stroke="var(--text-secondary)" strokeDasharray="5 4" label={{ value: 'set avg', fill: 'var(--text-muted)', fontSize: 11, position: 'insideTopLeft' }} />
                <ReferenceLine y={avg * (1 + lim)} stroke="var(--critical)" strokeDasharray="3 3" />
                <Bar dataKey="tension" name="tension" radius={[4, 4, 0, 0]} maxBarSize={48} isAnimationActive={false}>
                  {bars.map((b) => <Cell key={b.id} fill={b.status === 'faulty' ? 'var(--critical)' : b.status === 'watch' ? 'var(--warning)' : 'var(--series-1)'} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* c) why */}
        <div className="card" style={{ marginTop: 14 }}>
          <div className="card-head"><h3>{fault ? `Why is rope ${r.faulty_ropes[0]} faulty?` : 'How the ropes are sharing the load'}</h3></div>
          <p className="layman">{r.layman}</p>
          <div className="small muted">Wear multiplier = (1 + deviation)^b with b = {r.limits.feyrer_b} (Feyrer); days-to-limit from the 7-day trend of the deviation.</div>
        </div>

        {/* d) factors */}
        <div className="card" style={{ marginTop: 14 }}>
          <div className="card-head"><h3>What is affecting it</h3><span className="hint">ranked by severity · bar = healthy → alarm limit</span></div>
          <div className="factors">
            {r.factors.map((f) => (
              <div key={f.key} className="factor">
                <div className="factor-head"><strong>{f.label}</strong><span className="mono">{f.value_text}</span><span className="small muted">limit {f.limit_text}</span></div>
                <div className="meter"><span style={{ width: `${Math.round(f.severity * 100)}%`, background: f.severity >= 1 ? 'var(--critical)' : f.severity >= 0.5 ? 'var(--warning)' : 'var(--good)' }} /></div>
                <div className="small secondary">{f.meaning}</div>
              </div>))}
          </div>
        </div>

        <div className="grid g2" style={{ marginTop: 14 }}>
          {/* e) causes */}
          <div className="card"><div className="card-head"><h3>Possible causes</h3><span className="hint">hedged - confirm by inspection</span></div>
            <ul className="causes">{r.causes.map((c) => <li key={c}>{c}</li>)}</ul></div>
          {/* f) recommendation */}
          <div className={`card rec-card risk-${fault ? 'critical' : r.worst.deviation_pct >= r.limits.watch_pct ? 'medium' : 'good'}`}>
            <div className="card-head"><h3>Recommendation</h3></div>
            <div className="rec-action">{r.recommendation.action}</div>
            <div className="secondary">{r.recommendation.why}</div>
            <div className="rec-meta" style={{ marginTop: 8 }}>
              {r.recommendation.urgency_days != null && <span className="rec-chip"><span className="muted">Urgency</span> <strong>{r.recommendation.urgency_days} days</strong></span>}
              <span className="rec-chip"><span className="muted">Next checkup</span> <strong>{fmt.date(r.recommendation.next_checkup)}</strong></span>
              <span className="rec-chip"><span className="muted">Set life used</span> <strong>{r.set.life_used_pct}%</strong></span>
              <span className="rec-chip"><span className="muted">RUL</span> <strong>{fmt.rul(r.set.rul_days)}</strong></span>
              <span className="rec-chip"><span className="muted">Failure risk 30 d</span> <strong>{fmt.pct(r.set.failure_probability)}</strong></span>
            </div>
            <div className="small muted" style={{ marginTop: 8 }}>{r.recommendation.replace_as_set_note}</div>
          </div>
        </div>

        {/* g) sparkline grid */}
        <div className="card" style={{ marginTop: 14 }}>
          <div className="card-head"><h3>30-day per-rope tension</h3><span className="hint">daily medians, {days[0]} → today</span></div>
          <div className="spark-grid">
            {r.ropes.map((x, j) => (
              <div key={x.id} className={`spark-cell${faulty.has(x.id) ? ' fault' : ''}`}>
                <div className="row" style={{ justifyContent: 'space-between' }}><strong>{x.id}</strong><span className="small muted">{x.median_tension_kn.toFixed(2)} kN</span></div>
                <Spark values={r.series.map((s) => s.tensions[j])} highlight={faulty.has(x.id)} />
              </div>))}
          </div>
        </div>

        <div className="card" style={{ marginTop: 14 }}>
          <div className="card-head"><h3>Set-level summary</h3></div>
          <div className="grid g4 small">
            <div>Imbalance <b className="mono">{r.set.imbalance_pct.toFixed(1)} %</b></div>
            <div>Imbalance growth <b className="mono">{r.set.imbalance_growth_pct_per_day.toFixed(3)} %/day</b></div>
            <div>Vibration RMS <b className="mono">{r.set.vib_rms_g.toFixed(3)} g</b></div>
            <div>Sway band energy <b className="mono">{(r.set.sway_band_ratio * 100).toFixed(0)} %</b></div>
            <div>Life used (Feyrer) <b className="mono">{r.set.life_used_pct} %</b></div>
            <div>Health <b className="mono">{fmt.num(r.set.health_score, 0)}</b></div>
            <div>Failure probability <b className="mono">{fmt.pct(r.set.failure_probability)}</b></div>
            <div>Rules fired <b className="mono">{(r.set.rules_fired || []).map((x) => x.id || x).join(', ') || 'none'}</b></div>
          </div>
        </div>
        {/* h) ask */}
        <div className="row no-print" style={{ marginTop: 14 }}>
          <button className="btn primary" onClick={ask}><IconChat />Ask the assistant: "{r.assistant_question}"</button>
          <Link className="btn ghost" to={`/components/${r.component_id}`}>Open rope component{r.material?.label ? ` (${r.material.label})` : ''}</Link>
        </div>
      </section>
      <p className="small muted" style={{ marginTop: 20 }}>Decision support alongside technician inspection - not a certified safety system.</p>
    </div>
  )
}

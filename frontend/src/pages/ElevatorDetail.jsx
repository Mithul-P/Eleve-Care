import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import BackBar from '../components/BackBar'
import { AtRiskFilter, CriticalDot, RecommendationList, ReplaceTag, RiskBadge, RiskBanner } from '../components/risk'
import { ErrorBox, HealthMeter, HealthRing, Loading, Stat } from '../components/ui'
import { fmt, healthColor } from '../format'
import { IconAlert, IconChevron, IconInbox } from '../icons'
import { useLive, useLiveFetch } from '../live'
import { needsReplacement, riskOf } from '../risk'

function ComplaintsPanel({ elevatorId }) {
  const { data } = useLiveFetch(`/complaints?elevator_id=${elevatorId}`)
  const { pushToast } = useLive()
  const [busyId, setBusyId] = useState(null)
  const open = (data || []).filter((c) => c.status === 'open' || c.status === 'in-progress')
  const setStatus = async (c, status) => {
    setBusyId(c.id)
    try { await api(`/complaints/${c.id}`, { method: 'PATCH', body: { status } }) }
    catch (e) { pushToast({ level: 'warning', title: 'Update failed', text: e.message }) }
    finally { setBusyId(null) }
  }
  return (
    <div className="card">
      <div className="card-head"><IconInbox style={{ width: 16 }} /><h3>Complaint notifications</h3><span className="hint">{open.length} open</span>
        <div className="right"><Link to={`/report/${elevatorId}`} target="_blank" className="btn sm">Public form</Link></div></div>
      {open.length === 0 && <div className="empty">No open complaints.</div>}
      {open.map((c) => (
        <div key={c.id} className="notif info">
          <IconAlert className="ic" style={{ color: 'var(--warning)' }} />
          <div style={{ flex: 1 }}>
            <div className="notif-title">{c.description}</div>
            <div className="notif-msg">{fmt.dateTime(c.timestamp)} · {c.category} · {c.status}{c.submitted_without_login ? ' · public (no login)' : ''}</div>
            <div className="row" style={{ marginTop: 6 }}>
              {c.status === 'open' && <button className="btn sm" disabled={busyId === c.id} onClick={() => setStatus(c, 'in-progress')}>Acknowledge</button>}
              <button className="btn sm" disabled={busyId === c.id} onClick={() => setStatus(c, 'resolved')}>Resolve</button>
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

function AlertsPanel({ elevatorId }) {
  const { data } = useLiveFetch(`/notifications?elevator_id=${elevatorId}`)
  const alerts = data?.recent_alerts || []
  return (
    <div className="card">
      <div className="card-head"><IconAlert style={{ width: 16 }} /><h3>Status changes</h3><span className="hint">last 7 days</span></div>
      {alerts.length === 0 && <div className="empty">No status changes this week.</div>}
      {alerts.slice(0, 6).map((a) => (
        <Link to={`/components/${a.component_id}`} key={a.id} className={`notif ${a.severity}`}>
          <IconAlert className="ic" />
          <div>
            <div className="notif-title">{fmt.dateTime(a.timestamp)}</div>
            <div className="notif-msg">{a.message.length > 200 ? a.message.slice(0, 200) + '…' : a.message}</div>
          </div>
        </Link>
      ))}
    </div>
  )
}

function RecommendationsPanel({ elevatorId }) {
  const { data } = useLiveFetch(`/elevators/${elevatorId}/recommendations`)
  if (!data) return null
  return (
    <div className="card" id="recommendations">
      <div className="card-head">
        <h3>Recommendations</h3>
        <span className="hint">{data.actionable.length} need action - critical first</span>
      </div>
      <RecommendationList cards={data.cards} />
    </div>
  )
}

export default function ElevatorDetail() {
  const { id } = useParams()
  const [onlyAtRisk, setOnlyAtRisk] = useState(false)
  const { data: e, error } = useLiveFetch(`/elevators/${id}`)
  // The elevator's name isn't known until it loads, so the breadcrumb's last
  // part is a placeholder until then - the back button itself works either way.
  if (error && !e) return (
    <div>
      <BackBar trail={[{ label: 'Elevators', to: '/elevators' }]} />
      <ErrorBox error={error} />
    </div>
  )
  if (!e) return (
    <div>
      <BackBar trail={[{ label: 'Elevators', to: '/elevators' }]} />
      <Loading />
    </div>
  )
  const visible = onlyAtRisk ? e.components.filter(needsReplacement) : e.components
  const worst = e.components[0]
  return (
    <div>
      <BackBar trail={[{ label: 'Elevators', to: '/elevators' }, { label: e.name }]} />
      <div className="page-head">
        <HealthRing value={e.health_score} size={84} label="Overall" />
        <div>
          <div className="row"><h1>{e.name}</h1><span className="lift-type" title="Elev Care models traction lifts only">Traction lift · {e.drive_type || 'Gearless PM'}</span>{e.is_demo && <span className="demo-tag">Demo elevator</span>}<RiskBadge level={e.risk?.worst || 'good'} /></div>
          <div className="sub">{e.building} · {e.location} · {e.floors} floors · {e.capacity_kg} kg · {e.speed_mps} m/s</div>
          {e.demo_note && <div className="small" style={{ color: 'var(--accent)', marginTop: 4 }}>{e.demo_note}</div>}
        </div>
      </div>

      <nav className="subnav">
        <Link to={`/elevators/${id}/operational`} className="subnav-link">Operational data</Link>
        <Link to={`/elevators/${id}/cycles`} className="subnav-link">Usage cycles</Link>
        <Link to={`/energy?elevator=${id}`} className="subnav-link">Cost &amp; energy</Link>
        <Link to={`/elevators/${id}/materials`} className="subnav-link">Materials &amp; specification</Link>
        <Link to={`/elevators/${id}/sensors`} className="subnav-link">Sensor inputs</Link>
        <Link to={`/elevators/${id}/report`} className="subnav-link accent">Lift report</Link>
      </nav>

      {/* Section 5.4 - banner that scrolls to the components that need replacing */}
      <RiskBanner summary={e.risk} targetId="components" />

      <div className="grid g4" style={{ marginBottom: 16 }}>
        <Stat label="Critical - replace now" value={e.risk?.counts?.critical ?? 0}
          color={e.risk?.counts?.critical ? 'var(--critical)' : undefined} foot="failure imminent" />
        <Stat label="High risk - replace soon" value={e.risk?.counts?.high ?? 0}
          color={e.risk?.counts?.high ? 'var(--serious)' : undefined} foot="within this maintenance interval" />
        <Stat label="Shortest remaining life" value={fmt.rul(e.min_rul_days)} foot="across monitored components" />
        <Stat label="Last reading" value={fmt.ago(e.last_reading_at)} foot={fmt.dateTime(e.last_reading_at)} />
      </div>

      <div className="stack">
        <div className="card" style={{ padding: 0 }} id="components">
          <div className="card-head" style={{ padding: '14px 14px 0' }}>
            <h3>Components</h3>
            <span className="hint">sorted critical &rarr; high &rarr; medium &rarr; good</span>
            <div className="right">
              <AtRiskFilter value={onlyAtRisk} onChange={setOnlyAtRisk}
                count={e.components.filter(needsReplacement).length} />
            </div>
          </div>
          <div className="comp-row header">
            <span>Component</span><span>Status</span><span className="hide-sm">Health</span>
            <span className="hide-sm hide-md">Failure risk (30 d)</span><span className="hide-sm">Remaining life</span><span className="hide-sm hide-md">Next checkup</span><span />
          </div>
          {visible.length === 0 && <div className="empty">No components match this filter.</div>}
          {visible.map((c) => {
            // Section 5.1 - the whole row is tinted, the badge is bold, and a
            // REPLACE tag plus a pulsing dot mark the parts that need action.
            const level = riskOf(c)
            return (
              <Link key={c.id} to={`/components/${c.id}`} className={`comp-row row-${level}`}>
                <div style={{ minWidth: 0 }}>
                  <div className="comp-name">
                    <CriticalDot level={level} />
                    {c.name}
                    {c.type === 'rope' && c.faulty_ropes?.length > 0 && (
                      <span className="badge risk-critical sm" style={{ marginLeft: 8 }}>
                        Rope {c.faulty_ropes.join(', ')} — Fault Detected
                      </span>
                    )}
                    {c.type === 'rope' && c.watch_ropes?.length > 0 && (!c.faulty_ropes || c.faulty_ropes.length === 0) && (
                      <span className="badge risk-medium sm" style={{ marginLeft: 8 }}>
                        Rope {c.watch_ropes.join(', ')} — Requires Inspection
                      </span>
                    )}
                    <ReplaceTag level={level} />
                  </div>
                  <div className="comp-expl">{c.contributing_factors?.[0] ? `${c.contributing_factors[0].label}: ${c.contributing_factors[0].display_value}` : 'All indicators within healthy limits'}</div>
                  {c.material?.label && <div className="small muted comp-material">{c.material.label}</div>}
                </div>
                <div><RiskBadge level={level} /></div>
                <div className="hide-sm">
                  <div className="mono small" style={{ color: healthColor(c.health_score), marginBottom: 4 }}>{fmt.num(c.health_score)}</div>
                  <HealthMeter value={c.health_score} />
                </div>
                <div className="mono hide-sm hide-md">{fmt.pct(c.failure_probability)}</div>
                <div className="mono hide-sm">{fmt.rul(c.rul_days)}</div>
                <div className="small hide-sm hide-md" style={{ color: c.checkup_overdue ? 'var(--warning)' : 'var(--text-secondary)' }}>
                  {fmt.date(c.next_checkup_due)}{c.checkup_overdue ? ' \u00b7 overdue' : ''}
                </div>
                <IconChevron style={{ width: 16, color: 'var(--text-muted)' }} />
              </Link>
            )
          })}
        </div>

        <RecommendationsPanel elevatorId={id} />
        <div className="grid g2" style={{ alignItems: 'start' }}>
          <ComplaintsPanel elevatorId={id} />
          <AlertsPanel elevatorId={id} />
        </div>
      </div>
    </div>
  )
}

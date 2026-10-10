import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import { ErrorBox, HealthMeter, HealthRing, Loading, Stat, StatusBadge } from '../components/ui'
import { fmt, healthColor } from '../format'
import { IconAlert, IconChevron, IconInbox } from '../icons'
import { useLive, useLiveFetch } from '../live'

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

export default function ElevatorDetail() {
  const { id } = useParams()
  const { data: e, error } = useLiveFetch(`/elevators/${id}`)
  if (error && !e) return <ErrorBox error={error} />
  if (!e) return <Loading />
  const worst = e.components[0]
  return (
    <div>
      <div className="crumbs"><Link to="/elevators">Elevators</Link> / {e.name}</div>
      <div className="page-head">
        <HealthRing value={e.health_score} size={84} label="Overall" />
        <div>
          <div className="row"><h1>{e.name}</h1>{e.is_demo && <span className="demo-tag">Demo elevator</span>}<StatusBadge status={e.status} /></div>
          <div className="sub">{e.building} · {e.location} · {e.floors} floors · {e.capacity_kg} kg · {e.speed_mps} m/s</div>
          {e.demo_note && <div className="small" style={{ color: 'var(--accent)', marginTop: 4 }}>{e.demo_note}</div>}
        </div>
      </div>

      {worst && worst.status !== 'good' && (
        <div className={`callout ${worst.status}`} style={{ marginBottom: 16 }}>
          <strong style={{ color: 'var(--text-primary)' }}>{worst.name}: </strong>{worst.explanation}
        </div>
      )}

      <div className="grid g4" style={{ marginBottom: 16 }}>
        <Stat label="Components at risk" value={e.component_counts['at-risk']} color={e.component_counts['at-risk'] ? 'var(--critical)' : undefined} />
        <Stat label="Medium" value={e.component_counts.medium} />
        <Stat label="Shortest remaining life" value={fmt.rul(e.min_rul_days)} foot="across monitored components" />
        <Stat label="Last reading" value={fmt.ago(e.last_reading_at)} foot={fmt.dateTime(e.last_reading_at)} />
      </div>

      <div className="stack">
        <div className="card" style={{ padding: 0 }}>
          <div className="card-head" style={{ padding: '14px 14px 0' }}><h3>Components</h3><span className="hint">sorted at-risk → medium → good</span></div>
          <div className="comp-row header">
            <span>Component</span><span>Status</span><span className="hide-sm">Health</span>
            <span className="hide-sm hide-md">Failure risk (30 d)</span><span className="hide-sm">Remaining life</span><span className="hide-sm hide-md">Next checkup</span><span />
          </div>
          {e.components.map((c) => (
            <Link key={c.id} to={`/components/${c.id}`} className="comp-row">
              <div style={{ minWidth: 0 }}>
                <div className="comp-name">{c.name}</div>
                <div className="comp-expl">{c.contributing_factors?.[0] ? `${c.contributing_factors[0].label}: ${c.contributing_factors[0].display_value}` : 'All indicators within healthy limits'}</div>
              </div>
              <div><StatusBadge status={c.status} /></div>
              <div className="hide-sm">
                <div className="mono small" style={{ color: healthColor(c.health_score), marginBottom: 4 }}>{fmt.num(c.health_score)}</div>
                <HealthMeter value={c.health_score} />
              </div>
              <div className="mono hide-sm hide-md">{fmt.pct(c.failure_probability)}</div>
              <div className="mono hide-sm">{fmt.rul(c.rul_days)}</div>
              <div className="small hide-sm hide-md" style={{ color: c.checkup_overdue ? 'var(--warning)' : 'var(--text-secondary)' }}>
                {fmt.date(c.next_checkup_due)}{c.checkup_overdue ? ' · overdue' : ''}
              </div>
              <IconChevron style={{ width: 16, color: 'var(--text-muted)' }} />
            </Link>
          ))}
        </div>
        <div className="grid g2" style={{ alignItems: 'start' }}>
          <ComplaintsPanel elevatorId={id} />
          <AlertsPanel elevatorId={id} />
        </div>
      </div>
    </div>
  )
}

import { useEffect, useRef, useState } from 'react'
import { Link, NavLink, Outlet } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'
import { fmt } from '../format'
import { IconAlert, IconBell, IconBolt, IconChart, IconCpu, IconElevator, IconExternal, IconInbox, IconInfo, IconLeaf, IconLogout, IconPlay, Logo } from '../icons'
import { useLive, useLiveFetch } from '../live'
import ChatPanel from './ChatPanel'

function SimulatorControl() {
  const { sim, setSim, connected, pushToast } = useLive()
  const { user } = useAuth()
  const [busy, setBusy] = useState(false)
  const isAdmin = user?.role === 'admin'
  if (!sim) return <span className="badge neutral">Simulator offline</span>
  const realtime = sim.mode === 'real-time'

  const toggle = async (mode) => {
    if (mode === sim.mode) return
    setBusy(true)
    try { setSim(await api('/simulator/toggle-mode', { method: 'POST', body: { mode } })) }
    catch (e) { pushToast({ level: 'warning', title: 'Could not switch mode', text: e.message }) }
    finally { setBusy(false) }
  }
  const runBatch = async () => {
    setBusy(true)
    try {
      const r = await api('/simulator/run-batch', { method: 'POST' })
      setSim(r.status)
      pushToast({ level: 'info', title: 'Batch ingested', text: `${r.result.ingested} readings, ${r.result.predictions} predictions (${sim.batch_hours} h of data).` })
    } catch (e) { pushToast({ level: 'warning', title: 'Batch failed', text: e.message }) }
    finally { setBusy(false) }
  }

  return (
    <div className="row" style={{ gap: 8 }}>
      <span className="small muted" title="Simulation clock">Sim clock <span className="mono secondary">{fmt.dateTime(sim.sim_time)}</span></span>
      <div className="seg" role="group" aria-label="Data mode" title={isAdmin ? 'Switch ingestion mode' : 'Only admins can switch mode'}>
        <button className={!realtime ? 'on' : ''} disabled={!isAdmin || busy} onClick={() => toggle('batch')}>Batch · {sim.batch_hours}h</button>
        <button className={realtime ? 'on' : ''} disabled={!isAdmin || busy} onClick={() => toggle('real-time')}>Real-time</button>
      </div>
      {realtime ? (
        <span className="badge live"><span className="dot pulse" style={{ background: 'var(--critical)' }} />Live · {sim.realtime_interval_seconds}s</span>
      ) : (
        isAdmin && <button className="btn sm" disabled={busy} onClick={runBatch} title="Generate and ingest the next batch now"><IconPlay />Run batch now</button>
      )}
      <span className="dot" title={connected ? 'Stream connected' : 'Stream disconnected'} style={{ background: connected ? 'var(--good)' : 'var(--text-muted)' }} />
    </div>
  )
}

function NotificationBell() {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  const { data } = useLiveFetch('/notifications')
  useEffect(() => {
    const close = (e) => ref.current && !ref.current.contains(e.target) && setOpen(false)
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])
  const items = data?.items || []
  const urgent = (data?.counts?.critical || 0) + (data?.counts?.warning || 0)
  return (
    <div className="rel" ref={ref}>
      <button className="btn ghost" aria-label="Notifications" onClick={() => setOpen((o) => !o)}>
        <IconBell />
        {urgent > 0 && <span className="bell-count">{urgent}</span>}
      </button>
      {open && (
        <div className="popover" role="dialog" aria-label="Notifications">
          <div className="card-head" style={{ padding: '12px 12px 0' }}><h3>Notifications</h3><span className="hint">{items.length} active</span></div>
          {items.length === 0 && <div className="empty">All clear.</div>}
          {items.slice(0, 30).map((n, i) => {
            const Icon = n.level === 'info' ? IconInfo : IconAlert
            const to = n.component_id ? `/components/${n.component_id}` : n.kind === 'complaint' ? '/complaints' : `/elevators/${n.elevator_id}`
            return (
              <Link to={to} key={i} className={`notif ${n.level}`} onClick={() => setOpen(false)}>
                <Icon className="ic" />
                <div>
                  <div className="notif-title">{n.title}</div>
                  <div className="notif-msg">{n.message.length > 180 ? n.message.slice(0, 180) + '…' : n.message}</div>
                </div>
              </Link>
            )
          })}
        </div>
      )}
    </div>
  )
}

function Toasts() {
  const { toasts } = useLive()
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.level || ''}`}>
          <div style={{ fontWeight: 600 }}>{t.title}</div>
          <div className="secondary small">{t.text?.length > 200 ? t.text.slice(0, 200) + '…' : t.text}</div>
        </div>
      ))}
    </div>
  )
}

export default function Layout() {
  const { user, logout } = useAuth()
  const { data: complaints } = useLiveFetch('/complaints?status=open')
  const nav = [
    { to: '/elevators', label: 'Elevators', icon: IconElevator },
    { to: '/energy', label: 'Cost & energy', icon: IconBolt },
    { to: '/sustainability', label: 'Sustainability', icon: IconLeaf },
    { to: '/complaints', label: 'Complaints', icon: IconInbox, count: complaints?.length },
    { to: '/model', label: 'Model card', icon: IconCpu },
  ]
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <Logo className="brand-mark" />
          <div><div className="brand-name">Elev Care</div><div className="brand-sub">Suspension reliability</div></div>
        </div>
        {nav.map(({ to, label, icon: Icon, count }) => (
          <NavLink key={to} to={to} className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
            <Icon />{label}{count ? <span className="nav-count">{count}</span> : null}
          </NavLink>
        ))}
        <a className="nav-link" href="/report" target="_blank" rel="noreferrer"><IconExternal />Public complaint form</a>
        <div className="sidebar-foot">
          <div style={{ fontWeight: 600 }}>{user?.name}</div>
          <div className="muted">{user?.email} · {user?.role}</div>
          <button className="btn ghost sm" style={{ marginTop: 8, paddingLeft: 0 }} onClick={logout}><IconLogout />Sign out</button>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <IconChart style={{ width: 18, color: 'var(--text-muted)' }} />
          <span className="small muted">Decision support alongside technician inspection - not a certified safety system.</span>
          <div className="spacer" />
          <SimulatorControl />
          <NotificationBell />
        </header>
        <main className="content"><Outlet /></main>
      </div>
      <ChatPanel />
      <Toasts />
    </div>
  )
}

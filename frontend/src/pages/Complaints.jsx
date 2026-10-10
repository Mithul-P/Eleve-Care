import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { ErrorBox, Loading, Seg } from '../components/ui'
import { fmt } from '../format'
import { useLive, useLiveFetch } from '../live'

const STATUS_FILTERS = [
  { value: 'open,in-progress', label: 'Active' }, { value: 'resolved,dismissed', label: 'Closed' }, { value: '', label: 'All' },
]

export default function Complaints() {
  const [filter, setFilter] = useState('open,in-progress')
  const { data, error } = useLiveFetch(`/complaints${filter ? `?status=${filter}` : ''}`)
  const { pushToast } = useLive()
  const [busy, setBusy] = useState(null)
  const update = async (c, status) => {
    setBusy(c.id)
    try { await api(`/complaints/${c.id}`, { method: 'PATCH', body: { status } }) }
    catch (e) { pushToast({ level: 'warning', title: 'Update failed', text: e.message }) }
    finally { setBusy(null) }
  }
  return (
    <>
      <div className="page-head">
        <div><h1>Complaint log</h1><div className="sub">Reports submitted by passengers and building occupants through the public form (no login required).</div></div>
        <div className="actions"><a className="btn" href="/report" target="_blank" rel="noreferrer">Open public form</a></div>
      </div>
      <div className="row" style={{ marginBottom: 14 }}><Seg label="Status filter" value={filter} onChange={setFilter} options={STATUS_FILTERS} /></div>
      <ErrorBox error={error} />
      {!data ? <Loading /> : (
        <div className="card" style={{ padding: 0 }}>
          {data.length === 0 ? <div className="empty">No complaints in this view.</div> : (
            <table>
              <thead><tr><th>Submitted</th><th>Elevator</th><th>Category</th><th>Description</th><th>Status</th><th /></tr></thead>
              <tbody>
                {data.map((c) => (
                  <tr key={c.id}>
                    <td className="small">{fmt.dateTime(c.timestamp)}<div className="muted">{c.submitted_without_login ? 'public' : 'staff'}</div></td>
                    <td><Link to={`/elevators/${c.elevator_id}`} style={{ color: 'var(--accent)' }}>{c.elevator_name}</Link></td>
                    <td className="small">{c.category}</td>
                    <td style={{ maxWidth: 420 }}>{c.description}{c.contact && <div className="small muted">Contact: {c.contact}</div>}
                      {c.history?.length > 0 && <div className="small muted">Last update: {c.history[c.history.length - 1].by} · {fmt.dateTime(c.history[c.history.length - 1].at)}</div>}
                    </td>
                    <td><span className={`badge ${c.status === 'resolved' ? 'good' : c.status === 'open' ? 'medium' : 'neutral'}`}>{c.status}</span></td>
                    <td>
                      <div className="row" style={{ gap: 6, flexWrap: 'nowrap' }}>
                        {c.status === 'open' && <button className="btn sm" disabled={busy === c.id} onClick={() => update(c, 'in-progress')}>Acknowledge</button>}
                        {c.status !== 'resolved' && <button className="btn sm" disabled={busy === c.id} onClick={() => update(c, 'resolved')}>Resolve</button>}
                        {c.status === 'resolved' && <button className="btn sm ghost" disabled={busy === c.id} onClick={() => update(c, 'open')}>Reopen</button>}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </>
  )
}

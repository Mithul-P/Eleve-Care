// Public, no-login complaint form (e.g. linked from a QR code inside each car: /report/elv-03).
import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../api'
import { Logo } from '../icons'

const CATEGORIES = ['noise/vibration', 'jerky ride', 'door', 'stuck/trapped', 'other']

export default function ReportIssue() {
  const { elevatorId } = useParams()
  const [elevators, setElevators] = useState([])
  const [form, setForm] = useState({ elevator_id: elevatorId || '', category: 'noise/vibration', description: '', contact: '' })
  const [state, setState] = useState({ busy: false, error: null, done: null })

  useEffect(() => { api('/public/elevators', { auth: false }).then(setElevators).catch(() => {}) }, [])
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }))

  const submit = async (e) => {
    e.preventDefault()
    setState({ busy: true, error: null, done: null })
    try {
      const r = await api('/complaints', { method: 'POST', auth: false, body: { ...form, contact: form.contact || null } })
      setState({ busy: false, error: null, done: r })
      setForm((f) => ({ ...f, description: '', contact: '' }))
    } catch (err) { setState({ busy: false, error: err.message, done: null }) }
  }
  const selected = elevators.find((x) => x.id === form.elevator_id)

  return (
    <div className="center-page">
      <form className="card auth-card" style={{ maxWidth: 480 }} onSubmit={submit}>
        <div className="brand"><Logo className="brand-mark" /><div><div className="brand-name">Report a lift issue</div><div className="brand-sub">No account needed · goes straight to the maintenance team</div></div></div>
        {state.done && <div className="success" style={{ marginBottom: 14 }}>{state.done.message} Reference: <span className="mono">{state.done.id}</span></div>}
        {state.error && <div className="error">{state.error}</div>}
        {elevatorId && selected ? (
          <div className="field"><label>Elevator</label><div style={{ fontWeight: 600 }}>{selected.name}</div><div className="small muted">{selected.building}</div></div>
        ) : (
          <div className="field">
            <label htmlFor="elv">Elevator</label>
            <select id="elv" value={form.elevator_id} onChange={set('elevator_id')} required>
              <option value="" disabled>Select the lift…</option>
              {elevators.map((x) => <option key={x.id} value={x.id}>{x.name} - {x.building}</option>)}
            </select>
          </div>
        )}
        <div className="field">
          <label htmlFor="cat">What's wrong?</label>
          <select id="cat" value={form.category} onChange={set('category')}>{CATEGORIES.map((c) => <option key={c}>{c}</option>)}</select>
        </div>
        <div className="field">
          <label htmlFor="desc">Describe the issue</label>
          <textarea id="desc" rows={4} value={form.description} onChange={set('description')} minLength={10} maxLength={1000} required placeholder="e.g. The lift shakes when it starts moving from the ground floor." />
        </div>
        <div className="field">
          <label htmlFor="contact">Phone or email (optional)</label>
          <input id="contact" value={form.contact} onChange={set('contact')} maxLength={120} placeholder="Only if you'd like a follow-up" />
        </div>
        <button className="btn primary" style={{ width: '100%', justifyContent: 'center' }} disabled={state.busy}>{state.busy ? 'Sending…' : 'Submit report'}</button>
        <p className="note" style={{ marginTop: 14 }}>If someone is trapped, use the alarm button inside the lift car or call building security immediately.</p>
      </form>
    </div>
  )
}

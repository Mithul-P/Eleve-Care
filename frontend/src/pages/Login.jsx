import { useState } from 'react'
import { Link, Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../auth'
import { Logo } from '../icons'

export default function Login() {
  const { user, login } = useAuth()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  if (user) return <Navigate to={location.state?.from || '/elevators'} replace />

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true); setError(null)
    try { await login(email.trim(), password) } catch (err) { setError(err.message) } finally { setBusy(false) }
  }
  return (
    <div className="center-page">
      <form className="card auth-card" onSubmit={submit}>
        <div className="brand">
          <Logo className="brand-mark" />
          <div><div className="brand-name">Elev Care</div><div className="brand-sub">Predictive reliability for elevator suspension systems</div></div>
        </div>
        <h2 style={{ marginBottom: 14 }}>Staff sign in</h2>
        {error && <div className="error">{error}</div>}
        <div className="field"><label htmlFor="email">Email</label><input id="email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required /></div>
        <div className="field"><label htmlFor="pw">Password</label><input id="pw" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></div>
        <button className="btn primary" style={{ width: '100%', justifyContent: 'center' }} disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
        <p className="note" style={{ marginTop: 16 }}>Elevator data is restricted to admin and maintenance staff. Passengers can <Link to="/report" style={{ color: 'var(--accent)' }}>report an issue without an account</Link>.</p>
      </form>
    </div>
  )
}

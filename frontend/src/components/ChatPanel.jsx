import { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { api } from '../api'
import { IconChat, IconClose, IconSend } from '../icons'
import { useLive } from '../live'

const SUGGESTIONS = [
  'Which components are at risk?',
  "Why is the demo lift's rope at risk?",
  'What checkups are due?',
  'How long will the Lobby Lift traction machine last?',
]

// Renders **bold** safely (text nodes only - no HTML injection).
function RichText({ text }) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g)
  return parts.map((p, i) => (p.startsWith('**') && p.endsWith('**') ? <strong key={i}>{p.slice(2, -2)}</strong> : <span key={i}>{p}</span>))
}

export default function ChatPanel() {
  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [proactive, setProactive] = useState({ messages: [], llm_enabled: false })
  const [unread, setUnread] = useState(0)
  const bodyRef = useRef(null)
  const location = useLocation()
  const { subscribe } = useLive()

  // context from the current page
  const m = location.pathname.match(/^\/(elevators|components)\/([^/]+)/)
  const ctx = m ? (m[1] === 'elevators' ? { elevator_id: m[2] } : { component_id: m[2] }) : {}

  useEffect(() => {
    api('/chatbot/notifications').then((p) => {
      setProactive(p)
      setUnread(p.messages.filter((x) => x.level !== 'info').length)
    }).catch(() => {})
  }, [])

  // proactive push: status-change alerts arrive over the live stream
  useEffect(() => subscribe((msg) => {
    if (msg.type === 'alert') {
      setMessages((ms) => [...ms, { role: 'alert', level: msg.data.severity === 'critical' ? 'critical' : 'warning', content: msg.data.message }])
      setUnread((u) => u + 1)
    }
  }), [subscribe])

  useEffect(() => { if (open) setUnread(0) }, [open])
  useEffect(() => { bodyRef.current?.scrollTo(0, bodyRef.current.scrollHeight) }, [messages, open, busy])

  const send = async (text) => {
    const q = (text ?? input).trim()
    if (!q || busy) return
    setInput('')
    const history = messages.filter((x) => x.role === 'user' || x.role === 'assistant').map(({ role, content }) => ({ role, content }))
    setMessages((ms) => [...ms, { role: 'user', content: q }])
    setBusy(true)
    try {
      const r = await api('/chatbot/query', { method: 'POST', body: { message: q, history, ...ctx } })
      setMessages((ms) => [...ms, { role: 'assistant', content: r.answer, source: r.source }])
    } catch (e) {
      setMessages((ms) => [...ms, { role: 'assistant', content: `Sorry - ${e.message}`, source: 'error' }])
    } finally { setBusy(false) }
  }

  return (
    <>
      <button className="chat-fab" aria-label={open ? 'Close assistant' : 'Open assistant'} onClick={() => setOpen((o) => !o)}>
        {open ? <IconClose /> : <IconChat />}
        {!open && unread > 0 && <span className="bell-count">{unread}</span>}
      </button>
      {open && (
        <section className="chat-panel" aria-label="Elev Care assistant">
          <div className="chat-head">
            <IconChat style={{ width: 18, color: 'var(--accent)' }} />
            <div>
              <div style={{ fontWeight: 600 }}>Elev Care Assistant</div>
              <div className="small muted">{proactive.llm_enabled ? 'Claude · grounded in live fleet data' : 'Rule-based · grounded in live fleet data'}</div>
            </div>
          </div>
          <div className="chat-body" ref={bodyRef}>
            {proactive.messages.map((n, i) => (
              <div key={`p${i}`} className={`msg alert ${n.level}`}>
                <strong>{n.title}</strong>
                <div className="small secondary" style={{ marginTop: 3 }}>{n.message.length > 260 ? n.message.slice(0, 260) + '…' : n.message}</div>
              </div>
            ))}
            {messages.length === 0 && (
              <div className="msg bot">Hi! Ask me about any elevator's suspension health, failure risk, remaining life, checkups or complaints.{ctx.elevator_id || ctx.component_id ? ' I\'ll use the page you\'re on as context.' : ''}</div>
            )}
            {messages.map((msg, i) => (
              <div key={i} className={`msg ${msg.role === 'user' ? 'user' : msg.role === 'alert' ? `alert ${msg.level}` : 'bot'}`}>
                <RichText text={msg.content} />
                {msg.source && msg.source !== 'error' && <div className="src">{msg.source === 'claude' ? 'Claude' : 'Rule engine'}</div>}
              </div>
            ))}
            {busy && <div className="msg bot"><span className="typing"><span /><span /><span /></span></div>}
          </div>
          {messages.length === 0 && (
            <div className="chat-suggest">{SUGGESTIONS.map((s) => <button key={s} onClick={() => send(s)}>{s}</button>)}</div>
          )}
          <form className="chat-input" onSubmit={(e) => { e.preventDefault(); send() }}>
            <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask about health, RUL, checkups…" aria-label="Message" maxLength={2000} />
            <button className="btn primary" disabled={busy || !input.trim()} aria-label="Send"><IconSend /></button>
          </form>
        </section>
      )}
    </>
  )
}

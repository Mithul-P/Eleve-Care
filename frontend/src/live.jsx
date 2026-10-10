// Live event stream (WebSocket): simulator status, component updates, alerts, complaints.
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { api, wsUrl } from './api'

const LiveCtx = createContext(null)

export function LiveProvider({ children }) {
  const [connected, setConnected] = useState(false)
  const [sim, setSim] = useState(null)
  const [toasts, setToasts] = useState([])
  const [version, setVersion] = useState(0) // bumps on data-changing events so pages can refetch
  const listeners = useRef(new Set())

  const pushToast = useCallback((t) => {
    const id = Math.random().toString(36).slice(2)
    setToasts((ts) => [...ts.slice(-3), { id, ...t }])
    setTimeout(() => setToasts((ts) => ts.filter((x) => x.id !== id)), 7000)
  }, [])

  useEffect(() => {
    let ws, retry, closed = false
    let bumpTimer = null
    const bump = () => {
      if (bumpTimer) return
      bumpTimer = setTimeout(() => { bumpTimer = null; setVersion((v) => v + 1) }, 800)
    }
    const connect = () => {
      ws = new WebSocket(wsUrl())
      ws.onopen = () => setConnected(true)
      ws.onclose = () => {
        setConnected(false)
        if (!closed) retry = setTimeout(connect, 3000)
      }
      ws.onmessage = (ev) => {
        let msg
        try { msg = JSON.parse(ev.data) } catch { return }
        const { type, data } = msg
        if ((type === 'hello' || type === 'simulator') && data) setSim(data)
        if (type === 'alert') pushToast({ level: data.severity, title: 'Status change', text: data.message })
        if (type === 'complaint') pushToast({ level: 'info', title: `New complaint - ${data.elevator_name}`, text: data.description })
        if (['component_update', 'alert', 'complaint', 'complaint_update', 'maintenance'].includes(type)) bump()
        listeners.current.forEach((fn) => fn(msg))
      }
    }
    connect()
    const poll = setInterval(() => api('/simulator/status').then(setSim).catch(() => {}), 15000)
    api('/simulator/status').then(setSim).catch(() => {})
    return () => { closed = true; clearTimeout(retry); clearInterval(poll); ws && ws.close() }
  }, [pushToast])

  const subscribe = useCallback((fn) => {
    listeners.current.add(fn)
    return () => listeners.current.delete(fn)
  }, [])

  return (
    <LiveCtx.Provider value={{ connected, sim, setSim, toasts, pushToast, version, subscribe }}>
      {children}
    </LiveCtx.Provider>
  )
}

export const useLive = () => useContext(LiveCtx)

/** Fetch helper that refetches when live data changes (keeps previous data while loading). */
export function useLiveFetch(path, deps = []) {
  const { version } = useLive() || { version: 0 }
  const [state, setState] = useState({ path: null, data: null, error: null, loading: true })
  useEffect(() => {
    let alive = true
    // A null/empty path means "nothing to fetch yet" (e.g. the page is still
    // resolving which elevator to show) - stay in the loading state instead of
    // firing a request at a malformed URL.
    if (!path) return undefined
    setState((s) => ({ path, data: s.path === path ? s.data : null, error: null, loading: true }))
    api(path)
      .then((data) => alive && setState({ path, data, error: null, loading: false }))
      .catch((error) => alive && setState((s) => ({ ...s, error, loading: false })))
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, version, ...deps])
  return state.path === path && path ? state : { data: null, error: null, loading: Boolean(path) }
}

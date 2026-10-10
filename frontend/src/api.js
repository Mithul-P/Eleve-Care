// Thin fetch wrapper. All calls go to /api (proxied to FastAPI in dev, nginx in Docker).
const BASE = import.meta.env.VITE_API_BASE || '/api'
const TOKEN_KEY = 'elevcare.token'

export function getToken() {
  try { return localStorage.getItem(TOKEN_KEY) } catch { return null }
}
export function setToken(t) {
  try { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY) } catch { /* ignore */ }
}

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status }
}

export async function api(path, { method = 'GET', body, auth = true } = {}) {
  const headers = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const token = getToken()
  if (auth && token) headers.Authorization = `Bearer ${token}`
  const res = await fetch(`${BASE}${path}`, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined })
  if (res.status === 401 && auth) {
    setToken(null)
    window.dispatchEvent(new Event('elevcare:logout'))
  }
  if (!res.ok) {
    let msg = res.statusText
    try {
      const j = await res.json()
      msg = typeof j.detail === 'string' ? j.detail : Array.isArray(j.detail) ? j.detail.map((d) => d.msg).join('; ') : msg
    } catch { /* not json */ }
    throw new ApiError(res.status, msg)
  }
  return res.status === 204 ? null : res.json()
}

export function wsUrl() {
  const explicit = import.meta.env.VITE_WS_URL
  const base = explicit || `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/stream`
  return `${base}?token=${encodeURIComponent(getToken() || '')}`
}

// Back navigation + breadcrumbs for every page except login (Section 2).
//
// Two things matter here:
//
// 1. "Back" goes to the LOGICAL PARENT, not to browser history. If you arrive
//    at a component page from a notification, history-back would return you to
//    the notification, not to the elevator that component belongs to. The
//    parent is derived from the breadcrumb trail the page declares.
// 2. Filters survive the trip. Each page writes its week/month toggle into the
//    URL query string (see `useStickyParam`), so the browser back button, a
//    refresh and a shared link all restore the same view.

import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { IconChevron } from '../icons'

/**
 * A page filter stored in the URL rather than in component state.
 * Returns [value, setValue] like useState, so switching a page over is a
 * one-line change and the filter is automatically preserved on back/forward.
 */
export function useStickyParam(key, fallback) {
  const [params, setParams] = useSearchParams()
  const value = params.get(key) || fallback
  const set = (next) => {
    const p = new URLSearchParams(params)
    if (next == null || next === fallback) p.delete(key)
    else p.set(key, next)
    // replace: a filter change is not a new place, so it should not add a
    // history entry the user has to click back through.
    setParams(p, { replace: true })
  }
  return [value, set]
}

/**
 * @param {Array<{label: string, to?: string}>} trail
 *   Breadcrumb parts, root first, current page last (no `to` on the last one).
 *   The last part that HAS a `to` is where "Back" goes.
 */
export default function BackBar({ trail = [] }) {
  const navigate = useNavigate()
  const location = useLocation()

  const linked = trail.filter((t) => t.to)
  const parent = linked.length ? linked[linked.length - 1].to : '/elevators'

  const goBack = () => {
    // Prefer real history when it stays inside the app and points at the
    // parent anyway - that preserves scroll position. Otherwise jump to the
    // logical parent. Either way the user is never logged out and never
    // bounced to a blank page.
    if (location.key !== 'default' && window.history.length > 1) navigate(-1)
    else navigate(parent)
  }

  return (
    <div className="backbar">
      <button className="btn ghost sm backbtn" onClick={goBack} aria-label="Go back">
        <IconChevron style={{ width: 16, transform: 'rotate(180deg)' }} />
        Back
      </button>
      <nav className="crumbs" aria-label="Breadcrumb">
        {trail.map((part, i) => (
          <span key={`${part.label}-${i}`}>
            {i > 0 && <span className="crumb-sep" aria-hidden="true">/</span>}
            {part.to ? (
              <Link to={part.to}>{part.label}</Link>
            ) : (
              <span aria-current="page">{part.label}</span>
            )}
          </span>
        ))}
      </nav>
    </div>
  )
}

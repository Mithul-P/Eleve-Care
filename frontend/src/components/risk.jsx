// Shared risk UI: badge, replace tag, banner and recommendation card.
// Every one of these reads from ../risk.js, so a component's colour and label
// are identical on the fleet list, the elevator page, the charts, the
// recommendation cards and the chatbot (Sections 5 and 6).

import { Link } from 'react-router-dom'
import { fmt } from '../format'
import { IconAlert, IconCheck, IconChevron } from '../icons'
import { RISK, riskMeta, riskOf } from '../risk'

/** Status pill. Never colour alone: icon + text label + colour. */
export function RiskBadge({ level, component, size }) {
  const key = level || riskOf(component)
  const m = RISK[key] || RISK.good
  const Icon = key === 'good' ? IconCheck : IconAlert
  return (
    <span className={`badge risk-${key}${size === 'sm' ? ' sm' : ''}`} title={m.label}>
      <Icon />
      {m.badge}
    </span>
  )
}

/** The bold REPLACE tag shown beside at-risk rows and cards. */
export function ReplaceTag({ component, level }) {
  const key = level || riskOf(component)
  const m = RISK[key] || RISK.good
  if (!m.tag) return null
  return <span className={`replace-tag risk-${key}`}>{m.tag}</span>
}

/** Pulsing dot for critical items only - draws the eye without being noisy. */
export function CriticalDot({ level, component }) {
  const key = level || riskOf(component)
  if (key !== 'critical') return null
  return <span className="dot pulse crit-dot" style={{ background: 'var(--critical)' }} aria-hidden="true" />
}

/**
 * Top-of-page banner: "2 components need replacement".
 * Clicking it scrolls to the element with the given id (Section 5.4).
 */
export function RiskBanner({ summary, targetId = 'components' }) {
  if (!summary?.banner) return null
  const critical = summary.counts?.critical || 0
  const level = critical ? 'critical' : 'high'
  const scroll = () => {
    const el = document.getElementById(targetId)
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
  return (
    <button className={`risk-banner risk-${level}`} onClick={scroll}>
      <IconAlert />
      <strong>{summary.banner}</strong>
      <span className="secondary small">
        {critical > 0 && `${critical} critical`}
        {critical > 0 && summary.counts.high > 0 && ' · '}
        {summary.counts.high > 0 && `${summary.counts.high} high risk`}
      </span>
      <IconChevron style={{ width: 16, marginLeft: 'auto', transform: 'rotate(90deg)' }} />
    </button>
  )
}

/** "Show only at-risk" filter toggle used above risk tables. */
export function AtRiskFilter({ value, onChange, count }) {
  return (
    <label className="atrisk-filter">
      <input type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)} />
      Show only at-risk{count != null ? ` (${count})` : ''}
    </label>
  )
}

const ACTION_CLASS = {
  'REPLACE WHOLE UNIT': 'act-unit',
  'REPLACE COMPONENT': 'act-component',
  'INSPECT / ADJUST': 'act-inspect',
  LUBRICATE: 'act-inspect',
  MONITOR: 'act-monitor',
  'NO ACTION': 'act-none',
}

/**
 * One structured recommendation (Section 6). Replaces the old plain-text
 * advice: priority, one unambiguous action, the data behind it, a deadline,
 * a cost, and what happens if it is ignored.
 */
export function RecommendationCard({ rec, compact }) {
  const m = RISK[rec.priority] || RISK.good
  return (
    <article className={`rec-card risk-${rec.priority}${compact ? ' compact' : ''}`} id={`rec-${rec.component_id}`}>
      {/* 1. STATUS */}
      <div className="rec-section-status">
        <span className={`badge risk-${rec.priority}`}>
          <IconAlert />
          {rec.priority_label}
        </span>
        <CriticalDot level={rec.priority} />
      </div>

      {/* 2. COMPONENT */}
      <div className="rec-section-comp">
        <Link to={`/components/${rec.component_id}`} className="rec-component">{rec.component_name}</Link>
        <span className="muted small"> · {rec.elevator_name}</span>
        {rec.component_material && <div className="small muted">{rec.component_material}</div>}
      </div>

      {/* 3. RECOMMENDATION */}
      <div className="rec-section-recommendation">
        <div className={`rec-action ${ACTION_CLASS[rec.action] || ''}`}>{rec.action}</div>
        {!compact && rec.decision_why && (
          <div className="rec-why">
            <span className="rec-why-label">Decision basis</span>
            {rec.decision_why}
          </div>
        )}
      </div>

      {/* 4. KEY EVIDENCE */}
      <div className="rec-section-evidence">
        <div className="rec-block-label">KEY EVIDENCE</div>
        {rec.reasons?.length > 0 && (
          <ul className="rec-reasons">
            {rec.reasons.map((r) => <li key={r}>{r}</li>)}
          </ul>
        )}
        <div className="rec-meta" style={{ marginBottom: 0 }}>
          <span className="rec-chip"><span className="muted">Failure risk</span> <strong>{fmt.pct(rec.failure_probability)}</strong></span>
          <span className="rec-chip"><span className="muted">Remaining life</span> <strong>{fmt.rul(rec.rul_days)}</strong></span>
          {rec.cycle_usage_pct != null && (
            <span className="rec-chip"><span className="muted">Cycle life used</span> <strong>{fmt.num(rec.cycle_usage_pct, 0)}%</strong></span>
          )}
        </div>
      </div>

      {/* 5. URGENCY */}
      <div className="rec-section-urgency">
        <div className="rec-block-label">URGENCY</div>
        <div className="rec-urgency-val">
          {rec.deadline_days != null ? (
            <span className="rec-chip chip-urgency">
              <span className="muted">Due</span> <strong>Within {rec.deadline_days} day{rec.deadline_days === 1 ? '' : 's'}</strong>
            </span>
          ) : (
            <span className="rec-chip"><span className="muted">Timeline</span> <strong>Standard maintenance interval</strong></span>
          )}
        </div>
      </div>

      {/* 6. COST */}
      <div className="rec-section-cost">
        <div className="rec-block-label">COST</div>
        <div className="rec-cost-row">
          {rec.estimated_cost_inr != null && (
            <span className="rec-chip chip-cost"><span className="muted">Est. cost</span> <strong>{fmt.inr(rec.estimated_cost_inr)}</strong></span>
          )}
          {rec.emergency_cost_inr != null && rec.action.startsWith('REPLACE') && (
            <span className="rec-chip chip-cost-emergency"><span className="muted">If it fails in service</span> <strong>{fmt.inr(rec.emergency_cost_inr)}</strong></span>
          )}
        </div>
      </div>

      {/* 7. ACTION */}
      <div className="rec-section-action">
        <div className="rec-block-label">ACTION</div>
        {!compact && rec.priority !== 'good' && (
          <div className="rec-risk" style={{ marginBottom: 12 }}>
            {rec.risk_if_ignored && <div><span className="muted small">Risk if ignored</span><div>{rec.risk_if_ignored}</div></div>}
            {rec.safety_impact && <div><span className="muted small">Safety impact</span><div>{rec.safety_impact}</div></div>}
          </div>
        )}
        {rec.action === 'NO ACTION' && (
          <div className="muted small">No maintenance action required at this time.</div>
        )}
      </div>
    </article>
  )
}

/** A list of recommendation cards, critical first, with an empty state. */
export function RecommendationList({ cards, compact, onlyActionable = true }) {
  const rows = (cards || []).filter((c) => !onlyActionable || c.action !== 'NO ACTION')
  if (!rows.length) {
    return <div className="empty">No action required - all components are within healthy limits.</div>
  }
  return (
    <div className="rec-list">
      {rows.map((c) => <RecommendationCard key={c.component_id} rec={c} compact={compact} />)}
    </div>
  )
}

export { riskMeta }

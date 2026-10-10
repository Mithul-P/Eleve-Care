// ONE risk vocabulary for the whole UI (Section 5).
//
// Mirrors backend/app/services/risk.py exactly. The backend already sends a
// `risk` field on every component, so prefer `riskOf(component)` - the local
// classification exists only so that data which has not been through the
// backend annotator (a chart point, a chatbot payload) still renders with the
// same colours and labels as everything else.

export const RISK_ORDER = { critical: 0, high: 1, medium: 2, good: 3 }

export const THRESHOLD = {
  failureProbability: { critical: 60, high: 35, medium: 15 },
  rulDays: { critical: 14, high: 45, medium: 120 },
}

export const RISK = {
  critical: {
    key: 'critical',
    label: 'Critical - replace now',
    short: 'REPLACE NOW',
    badge: 'Critical',
    color: 'var(--critical)',
    soft: 'var(--critical-soft)',
    tag: 'REPLACE',
    icon: '\u25CF',          // filled dot; paired with text, never colour alone
  },
  high: {
    key: 'high',
    label: 'High risk - replace soon',
    short: 'REPLACE SOON',
    badge: 'High',
    color: 'var(--serious)',
    soft: 'rgba(236, 131, 90, 0.15)',
    tag: 'REPLACE',
    icon: '\u25B2',
  },
  medium: {
    key: 'medium',
    label: 'Medium - monitor',
    short: 'MONITOR',
    badge: 'Medium',
    color: 'var(--warning)',
    soft: 'var(--warning-soft)',
    tag: null,
    icon: '\u25C6',
  },
  good: {
    key: 'good',
    label: 'Good',
    short: 'NO ACTION',
    badge: 'Good',
    color: 'var(--good)',
    soft: 'var(--good-soft)',
    tag: null,
    icon: '\u2713',
  },
}

/** Classify from raw numbers - the worse of probability and remaining life wins. */
export function classify(failureProbability, rulDays, status) {
  const p = failureProbability ?? 0
  const r = rulDays ?? 10000
  if (p >= THRESHOLD.failureProbability.critical || r <= THRESHOLD.rulDays.critical) return 'critical'
  if (p >= THRESHOLD.failureProbability.high || r <= THRESHOLD.rulDays.high) return 'high'
  if (p >= THRESHOLD.failureProbability.medium || r <= THRESHOLD.rulDays.medium) return 'medium'
  if (status === 'at-risk') return 'high'
  if (status === 'medium') return 'medium'
  return 'good'
}

/** Prefer the backend's own classification; fall back to classifying locally. */
export function riskOf(c) {
  if (!c) return 'good'
  return c.risk || classify(c.failure_probability, c.rul_days, c.status)
}

export const riskMeta = (c) => RISK[riskOf(c)] || RISK.good

/** Critical first, then high, medium, good; worst health first inside a level. */
export function sortByRisk(rows) {
  return [...rows].sort(
    (a, b) => RISK_ORDER[riskOf(a)] - RISK_ORDER[riskOf(b)] || (a.health_score ?? 100) - (b.health_score ?? 100),
  )
}

export const needsReplacement = (c) => ['critical', 'high'].includes(riskOf(c))

/** Counts per level plus the banner text for the top of an elevator page. */
export function summariseRisk(rows) {
  const counts = { critical: 0, high: 0, medium: 0, good: 0 }
  rows.forEach((r) => { counts[riskOf(r)] += 1 })
  const n = counts.critical + counts.high
  return {
    counts,
    needsReplacement: n,
    banner: n ? `${n} component${n > 1 ? 's' : ''} need${n > 1 ? '' : 's'} replacement` : null,
  }
}

/** Colour for a chart series or bar, given a component or a risk key. */
export function riskColor(c) {
  return (typeof c === 'string' ? RISK[c] || RISK.good : riskMeta(c)).color
}

import BackBar, { useStickyParam } from '../components/BackBar'
import { RecommendationList } from '../components/risk'
import { ErrorBox, Loading, Seg, Stat } from '../components/ui'
import { fmt } from '../format'
import { useLiveFetch } from '../live'

// Section 6 - fleet-wide recommendations, critical first.
//
// The same RecommendationCard component renders here, on the elevator page and
// in the chatbot, so a recommendation looks and reads identically everywhere.

const FILTERS = [
  { value: 'action', label: 'Needs action' },
  { value: 'replace', label: 'Replacement only' },
  { value: 'all', label: 'All components' },
]

export default function Recommendations() {
  const [filter, setFilter] = useStickyParam('filter', 'action')
  const { data, error } = useLiveFetch('/analytics/recommendations')

  const trail = [{ label: 'Elevators', to: '/elevators' }, { label: 'Recommendations' }]
  if (error && !data) return (
    <>
      <BackBar trail={trail} />
      <ErrorBox error={error} />
    </>
  )
  if (!data) return (
    <>
      <BackBar trail={trail} />
      <Loading />
    </>
  )

  const all = data.elevators.flatMap((e) => e.cards)
  const rows = filter === 'all' ? all
    : filter === 'replace' ? all.filter((c) => c.action.startsWith('REPLACE'))
      : all.filter((c) => c.action !== 'NO ACTION')

  const counts = {
    critical: all.filter((c) => c.priority === 'critical').length,
    high: all.filter((c) => c.priority === 'high').length,
    unit: all.filter((c) => c.action === 'REPLACE WHOLE UNIT').length,
    component: all.filter((c) => c.action === 'REPLACE COMPONENT').length,
  }
  const spend = rows.reduce((a, c) => a + (c.estimated_cost_inr || 0), 0)

  return (
    <>
      <BackBar trail={trail} />
      <div className="page-head">
        <div>
          <h1>Maintenance recommendations</h1>
          <div className="sub">Every at-risk component across the fleet, with the replace-component vs replace-unit decision and the rule that produced it.</div>
        </div>
        <div className="right"><Seg label="Filter" value={filter} onChange={setFilter} options={FILTERS} /></div>
      </div>

      <div className="grid g4 kpi-grid" style={{ marginBottom: 16 }}>
        <Stat label="Critical" value={counts.critical} foot="replace now"
          color={counts.critical ? 'var(--critical)' : undefined} />
        <Stat label="High risk" value={counts.high} foot="replace soon"
          color={counts.high ? 'var(--serious)' : undefined} />
        <Stat label="Whole-unit replacements" value={counts.unit} foot={`${counts.component} component-only`} />
        <Stat label="Planned spend" value={fmt.inr(spend)} foot="sum of the recommendations shown" />
      </div>

      <RecommendationList cards={rows} onlyActionable={false} />
    </>
  )
}

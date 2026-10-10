import { useLiveFetch } from '../live'

/**
 * Shared ElevatorSelect component (Section 5).
 * Supports "All elevators" plus individual elevators.
 * Integrates with useStickyParam for URL persistence across Energy, Cycles, and Sustainability.
 */
export default function ElevatorSelect({
  value,
  onChange,
  elevators: propElevators,
  includeAll = true,
  allLabel = 'All elevators',
  style,
  className = '',
  ariaLabel = 'Select elevator',
}) {
  const { data: fetchedElevators } = useLiveFetch(propElevators ? null : '/elevators')
  const list = propElevators || fetchedElevators || []

  return (
    <select
      value={value ?? ''}
      onChange={(e) => onChange && onChange(e.target.value)}
      className={`elevator-select ${className}`}
      style={{ width: 'auto', minWidth: 180, margin: 0, ...style }}
      aria-label={ariaLabel}
    >
      {includeAll && <option value="">{allLabel}</option>}
      {list.map((e) => (
        <option key={e.id || e._id} value={e.id || e._id}>
          {e.name}
        </option>
      ))}
    </select>
  )
}

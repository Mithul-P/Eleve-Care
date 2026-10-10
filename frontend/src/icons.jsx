// Minimal inline icon set (stroke icons, currentColor).
const P = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round' }
const I = (d) => (props) => <svg viewBox="0 0 24 24" {...P} {...props} aria-hidden="true">{d}</svg>

export const IconElevator = I(<><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M12 3v18M8 8l1.5-2L11 8M13 16l1.5 2 1.5-2" /></>)
export const IconBolt = I(<path d="M13 2 4 14h7l-1 8 9-12h-7z" />)
export const IconLeaf = I(<><path d="M5 21c0-9 5-15 15-16-1 10-7 15-15 16z" /><path d="M5 21 13 13" /></>)
export const IconInbox = I(<><path d="M3 13h5l1.5 3h5L16 13h5" /><path d="M5 5h14l2 8v6H3v-6z" /></>)
export const IconChart = I(<><path d="M4 20V10M10 20V4M16 20v-7M22 20H2" /></>)
export const IconBell = I(<><path d="M6 16V11a6 6 0 1 1 12 0v5l2 2H4z" /><path d="M10 21h4" /></>)
export const IconChat = I(<><path d="M4 5h16v11H9l-5 4z" /><path d="M8 10h8M8 13h5" /></>)
export const IconClose = I(<path d="M6 6l12 12M18 6 6 18" />)
export const IconSend = I(<path d="M4 12 20 4l-6 16-3-7z" />)
export const IconCheck = I(<path d="m5 12 4 4 10-10" />)
export const IconAlert = I(<><path d="M12 3 2 20h20z" /><path d="M12 10v4M12 17h.01" /></>)
export const IconX = I(<><circle cx="12" cy="12" r="9" /><path d="m9 9 6 6M15 9l-6 6" /></>)
export const IconInfo = I(<><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></>)
export const IconChevron = I(<path d="m9 6 6 6-6 6" />)
export const IconPlay = I(<path d="M7 5v14l11-7z" />)
export const IconWrench = I(<path d="M14 6a4 4 0 0 0 5 5l-9 9-4-1-1-4 9-9z" />)
export const IconLogout = I(<><path d="M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10" /></>)
export const IconExternal = I(<><path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6" /></>)
export const IconCpu = I(<><rect x="6" y="6" width="12" height="12" rx="2" /><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4" /></>)
export const IconLayers = I(<><path d="M12 3 2 9l10 6 10-6z" /><path d="M2 15l10 6 10-6" /><path d="M2 12l10 6 10-6" /></>)

export function Logo(props) {
  return (
    <svg viewBox="0 0 32 32" {...props} aria-hidden="true">
      <rect width="32" height="32" rx="8" fill="#1b2129" />
      <path d="M10 6v20M22 6v20" stroke="#3987e5" strokeWidth="2.5" strokeLinecap="round" />
      <rect x="12.5" y="12" width="7" height="9" rx="1.5" fill="#19b37a" />
    </svg>
  )
}

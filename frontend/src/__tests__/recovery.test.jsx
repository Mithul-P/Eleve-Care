import { afterEach, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { api } from '../api'
import { useLiveFetch } from '../live'
import ReportIssue from '../pages/ReportIssue'
vi.mock('../api', () => ({ api: vi.fn(), wsUrl: vi.fn() }))
globalThis.IS_REACT_ACT_ENVIRONMENT = true
let root, div
function setup() { div = document.createElement('div'); document.body.appendChild(div); root = createRoot(div) }
afterEach(async () => { await act(async () => root?.unmount()); div?.remove(); vi.resetAllMocks() })
it('clears previous lift data and ignores late responses after navigation', async () => {
  setup()
  const pending = {}
  api.mockImplementation((path) => new Promise((resolve) => { pending[path] = resolve }))
  function View({ path }) { const { data, loading } = useLiveFetch(path); return <div>{data?.name || (loading ? 'Loading' : 'Idle')}</div> }
  await act(async () => root.render(<View path="/a" />))
  await act(async () => pending['/a']({ name: 'Lift A' }))
  expect(div.textContent).toBe('Lift A')
  await act(async () => root.render(<View path="/b" />))
  expect(div.textContent).toBe('Loading')
  await act(async () => root.render(<View path="/c" />))
  await act(async () => pending['/b']({ name: 'Lift B' }))
  expect(div.textContent).toBe('Loading')
  await act(async () => pending['/c']({ name: 'Lift C' }))
  expect(div.textContent).toBe('Lift C')
  await act(async () => root.render(<View path={null} />))
  expect(div.textContent).toBe('Idle')
})
it('recovers the public lift list after failure and requires a valid selection', async () => {
  setup()
  api.mockRejectedValueOnce(new Error('Offline')).mockResolvedValueOnce([{ id: 'elv-01', name: 'Lift One', building: 'Tower' }])
  await act(async () => root.render(<MemoryRouter><ReportIssue /></MemoryRouter>))
  expect(div.querySelector('[role="alert"]').textContent).toContain('could not load')
  expect(div.querySelector('button.primary').disabled).toBe(true)
  await act(async () => div.querySelector('button[type="button"]').click())
  expect(div.querySelector('[role="alert"]')).toBeNull()
  expect(div.querySelector('button.primary').disabled).toBe(true)
  await act(async () => { const select = div.querySelector('#elv'); select.value = 'elv-01'; select.dispatchEvent(new Event('change', { bubbles: true })) })
  expect(div.querySelector('button.primary').disabled).toBe(false)
})

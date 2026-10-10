// Render smoke tests for the Round-5 pages against real API responses (fixtures dumped from the backend test client).
import { describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import elevator from './fixtures/elevator.json'
import elevators from './fixtures/elevators.json'
import report from './fixtures/report.json'
import matspecElev from './fixtures/matspec_elev.json'
import matspecRope from './fixtures/matspec_rope.json'
import matspecTraction from './fixtures/matspec_traction.json'
import sensorsMeta from './fixtures/sensors_meta.json'
import sensors from './fixtures/sensors.json'
import sensorsLive from './fixtures/sensors_live.json'
import sensorSeries from './fixtures/sensor_series.json'
import model from './fixtures/model.json'

const ROUTES = {
  '/elevators/elv-03': elevator, '/elevators': elevators, '/elevators/elv-03/reports/suspension-rope': report,
  '/elevators/elv-03/material-spec': matspecElev, '/components/elv-03-rope/material-spec': matspecRope,
  '/components/elv-03-traction/material-spec': matspecTraction, '/sensors/meta': sensorsMeta,
  '/elevators/elv-03/sensors': sensors, '/elevators/elv-03/sensors/live': sensorsLive, '/elevators/elv-03/sensors/S-07/series': sensorSeries,
  '/analytics/model': model,
}
vi.mock('../live', () => ({
  useLive: () => ({ connected: true, subscribe: () => () => {}, pushToast: () => {}, sim: null, setSim: () => {}, toasts: [], version: 0 }),
  useLiveFetch: (path) => ({ data: path ? ROUTES[path] ?? null : null, error: path && !(path in ROUTES) ? new Error(`no fixture for ${path}`) : null, loading: false }),
}))
vi.mock('../api', () => ({ api: vi.fn(async () => ({})) }))

globalThis.IS_REACT_ACT_ENVIRONMENT = true
window.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }

const { default: LiftReport } = await import('../pages/LiftReport')
const { default: MaterialSpec } = await import('../pages/MaterialSpec')
const { default: Sensors } = await import('../pages/Sensors')
const { default: ModelCard } = await import('../pages/ModelCard')
const { default: ElevatorDetail } = await import('../pages/ElevatorDetail')

const mount = async (path) => {
  const div = document.createElement('div'); document.body.appendChild(div)
  const root = createRoot(div)
  await act(async () => {
    root.render(<MemoryRouter initialEntries={[path]}><Routes>
      <Route path="/elevators/:id" element={<ElevatorDetail />} />
      <Route path="/elevators/:id/report" element={<LiftReport />} />
      <Route path="/elevators/:id/materials" element={<MaterialSpec />} />
      <Route path="/elevators/:id/sensors" element={<Sensors />} />
      <Route path="/model" element={<ModelCard />} />
    </Routes></MemoryRouter>)
  })
  const html = div.innerHTML
  await act(async () => root.unmount())
  return html
}

describe('Round-5 pages render from real API shapes', () => {
  it('lift report names the faulty rope with layman text, factors, causes and recommendation', async () => {
    const h = await mount('/elevators/elv-03/report')
    expect(h).toContain('FAULT DETECTED: Rope R6 (of 6)')
    expect(h).toContain('stretcher'); expect(h).toContain('What is affecting it'); expect(h).toContain('possible:')
    expect(h).toContain('Recommendation'); expect(h).toContain('Ask the assistant'); expect(h).toContain('30-day per-rope tension')
    expect(h).toContain('Traction lift')
  })
  it('materials & specification page shows every component with units and default tags', async () => {
    const h = await mount('/elevators/elv-03/materials')
    expect(h).toContain('Materials &amp; specification'); expect(h).toContain('Material grade'); expect(h).toContain('g/m²')
    expect(h).toContain('using default value'); expect(h).toContain('Drive type'); expect(h).toContain('Fill typical parts')
  })
  it('sensor inputs page shows registry, live monitor, coverage and tools', async () => {
    const h = await mount('/elevators/elv-03/sensors')
    expect(h).toContain('Live stream monitor'); expect(h).toContain('Sensor registry'); expect(h).toContain('Coverage checklist')
    expect(h).toContain('Rope 1 tension load cell'); expect(h).toContain('Ingestion'); expect(h).toContain('WebSocket connected')
  })
  it('model card lists physics inputs with their source', async () => {
    const h = await mount('/model')
    expect(h).toContain('Physics inputs from material specifications'); expect(h).toContain('user spec'); expect(h).toContain('default')
  })
  it('elevator page carries the traction badge and links to the new pages', async () => {
    const h = await mount('/elevators/elv-03')
    expect(h).toContain('Traction lift'); expect(h).toContain('/elevators/elv-03/report'); expect(h).toContain('/elevators/elv-03/sensors'); expect(h).toContain('/elevators/elv-03/materials')
    expect(h).not.toMatch(/hydraul/i)
  })
})

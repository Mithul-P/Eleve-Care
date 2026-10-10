// Prototype persistence for lifts created through the "Set up a Traction
// Lift" wizard. These lifts are NOT part of the backend/Mongo fleet the rest
// of the app reads from (that fleet is fixed, seeded, ML-driven data) - they
// live in this browser's localStorage until/unless a backend endpoint is
// built to adopt them. This is called out again in the wizard's disclaimer.
import { useCallback, useEffect, useState } from 'react'
import { COMPONENT_TYPES } from './elevatorConfig'

const KEY = 'elevcare.customLifts.v1'

function readAll() {
  try {
    const raw = localStorage.getItem(KEY)
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {}
  }
}

function writeAll(all) {
  try { localStorage.setItem(KEY, JSON.stringify(all)) } catch { /* storage unavailable - proceed in-memory only */ }
}

function emit() {
  window.dispatchEvent(new Event('elevcare:customlifts'))
}

export function listLifts() {
  return Object.values(readAll()).sort((a, b) => (b.createdAt || '').localeCompare(a.createdAt || ''))
}

export function getLift(id) {
  return readAll()[id] || null
}

export function newLiftId() {
  return `lift-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`
}

export function createLift(fields) {
  const id = newLiftId()
  const components = {}
  COMPONENT_TYPES.forEach((type) => { components[type] = { scenario: 'inherit', spec: {} } })
  const lift = { id, ...fields, createdAt: new Date().toISOString(), components }
  const all = readAll()
  all[id] = lift
  writeAll(all)
  emit()
  return lift
}

export function saveLift(lift) {
  const all = readAll()
  all[lift.id] = lift
  writeAll(all)
  emit()
}

export function deleteLift(id) {
  const all = readAll()
  delete all[id]
  writeAll(all)
  emit()
}

/** Reactive list of custom lifts (updates across the app when one is created/edited/deleted). */
export function useCustomLifts() {
  const [lifts, setLifts] = useState(() => listLifts())
  useEffect(() => {
    const refresh = () => setLifts(listLifts())
    window.addEventListener('elevcare:customlifts', refresh)
    window.addEventListener('storage', refresh)
    return () => { window.removeEventListener('elevcare:customlifts', refresh); window.removeEventListener('storage', refresh) }
  }, [])
  return lifts
}

/** Reactive single lift, with a setter that persists immediately. */
export function useCustomLift(id) {
  const [lift, setLiftState] = useState(() => (id ? getLift(id) : null))
  useEffect(() => {
    const refresh = () => setLiftState(id ? getLift(id) : null)
    refresh()
    window.addEventListener('elevcare:customlifts', refresh)
    window.addEventListener('storage', refresh)
    return () => { window.removeEventListener('elevcare:customlifts', refresh); window.removeEventListener('storage', refresh) }
  }, [id])
  const update = useCallback((updater) => {
    setLiftState((prev) => {
      const base = prev || getLift(id)
      if (!base) return prev
      const next = typeof updater === 'function' ? updater(base) : { ...base, ...updater }
      saveLift(next)
      return next
    })
  }, [id])
  return [lift, update]
}

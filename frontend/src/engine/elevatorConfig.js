// src/engine/elevatorConfig.js
//
// Engine for the "Set up a Traction Lift" wizard (9 components, full material
// specification per component). Every constant below is a documented default
// that a blank field falls back to - mirroring ml/elevcare_ml/materials.py's
// resolve()/completeness() pattern for the original 4-component simulator,
// just extended to all 9 traction-lift components this wizard configures.
//
// IMPORTANT: this is a self-contained prototype/decision-support layer for
// lifts created through this wizard. It does not feed the trained ML model or
// the Mongo-backed simulator that power the rest of the dashboard (those only
// know about rope/sheave/bearing/traction). Every parameter here is editable
// in this one file, exactly like the rest of the app's physics engine.

// ---------------------------------------------------------------------------
// Components
// ---------------------------------------------------------------------------

export const COMPONENTS = [
  { type: 'rope', name: 'Suspension Rope', model: 'fatigue' },
  { type: 'belt', name: 'Suspension Belt', model: 'fatigue' },
  { type: 'sheave', name: 'Traction Sheave', model: 'fatigue' },
  { type: 'motor', name: 'Motor', model: 'fatigue' },
  { type: 'brake', name: 'Brake System', model: 'wear' },
  { type: 'rails', name: 'Guide Rails', model: 'fatigue' },
  { type: 'guide_shoes', name: 'Guide Shoes/Rollers', model: 'wear' },
  { type: 'counterweight', name: 'Counterweight Frame', model: 'fatigue' },
  { type: 'rope_end', name: 'Rope End Connections', model: 'fatigue' },
]
export const COMPONENT_TYPES = COMPONENTS.map((c) => c.type)
export const COMPONENT_LABEL = Object.fromEntries(COMPONENTS.map((c) => [c.type, c.name]))
export const COMPONENT_MODEL = Object.fromEntries(COMPONENTS.map((c) => [c.type, c.model]))

// ---------------------------------------------------------------------------
// Step 1 - lift-level defaults (every field pre-filled so the form can be
// submitted with zero edits, per the wizard's acceptance criteria)
// ---------------------------------------------------------------------------

export const LIFT_DEFAULTS = {
  name: '',
  floors: 10,
  capacity_kg: 1000,
  speed_mps: 1.5,
  install_date: new Date().toISOString().slice(0, 10),
  trips_per_day: 120,
  rope_count: 6,
  rope_diameter_mm: 10,
  sheave_diameter_mm: 400,
  scenario: 'auto',
}

export const SCENARIOS = ['good', 'medium', 'risk']
// Auto = seeded mix applied across the 9 components (~60/25/15).
export const AUTO_MIX = { good: 0.6, medium: 0.25, risk: 0.15 }

/** Deterministic pseudo-random 0..1 from a string seed (so "Auto" is stable per lift, not re-rolled every render). */
function seededRandom(seed) {
  let h = 0
  for (let i = 0; i < seed.length; i++) { h = (h << 5) - h + seed.charCodeAt(i); h |= 0 }
  return () => {
    h = (h * 1103515245 + 12345) & 0x7fffffff
    return h / 0x7fffffff
  }
}

/** The Auto scenario for one component: a seeded draw from AUTO_MIX. */
export function autoScenarioFor(liftId, componentType) {
  const rnd = seededRandom(`${liftId}:${componentType}`)()
  if (rnd < AUTO_MIX.good) return 'good'
  if (rnd < AUTO_MIX.good + AUTO_MIX.medium) return 'medium'
  return 'risk'
}

// ---------------------------------------------------------------------------
// 1a. Material specification - 8 common fields + component-specific extras
// ---------------------------------------------------------------------------

export const FIELDS = [
  { key: 'material_type', label: 'Material type', unit: '' },
  { key: 'material_grade', label: 'Material grade', unit: '' },
  { key: 'gsm', label: 'GSM', unit: 'g/m²' },
  { key: 'thickness_mm', label: 'Thickness', unit: 'mm' },
  { key: 'composition', label: 'Composition', unit: '' },
  { key: 'weight', label: 'Weight', unit: 'kg (kg/m for rope/belt)' },
  { key: 'strength', label: 'Strength', unit: 'MPa / kN' },
  { key: 'coating', label: 'Coating', unit: '' },
]
export const FIELD_KEYS = FIELDS.map((f) => f.key)

// GSM only makes sense for belt / liner / lining / PU-type materials.
const GSM_APPLIES = /belt|liner|lining|polyurethane|PU/i
export function gsmApplicable(type, materialType) {
  if (type === 'sheave' || type === 'guide_shoes' || type === 'brake') return GSM_APPLIES.test(materialType || '')
  return GSM_APPLIES.test(materialType || '')
}

// Extra, component-specific fields with real physical parameters. `live: true`
// means the field actually feeds computeMetrics() below; `live: false` means
// it is collected for completeness/provenance only (no degradation law
// consumes it yet) - each is labelled as such in the material-spec form too.
export const EXTRA_FIELDS = {
  rope: [
    { key: 'rope_count', label: 'Number of ropes', unit: '', default: 6, min: 4, max: 8, live: true },
    { key: 'rope_diameter_mm', label: 'Rope diameter', unit: 'mm', default: 10, live: true },
    { key: 'breaking_load_kn', label: 'Minimum breaking load', unit: 'kN', default: 60, live: true },
    { key: 'tensile_grade', label: 'Wire tensile grade (EN 12385-5)', unit: 'N/mm²', default: '1570/1770', live: false },
  ],
  belt: [
    { key: 'belt_dims', label: 'Belt width x thickness', unit: 'mm', default: '30 x 3', live: false },
    { key: 'breaking_load_kn', label: 'Minimum breaking load', unit: 'kN', default: 90, live: true },
    { key: 'tensile_grade', label: 'Steel-cord tensile grade', unit: 'N/mm²', default: '1770', live: false },
  ],
  sheave: [
    { key: 'groove_type', label: 'Groove type', unit: '', default: 'Undercut U', options: ['U', 'Undercut U', 'V'], live: false },
    { key: 'sheave_diameter_mm', label: 'Sheave diameter', unit: 'mm', default: 400, live: true },
    { key: 'groove_diameter_mm', label: 'Groove diameter', unit: 'mm', default: 10.5, live: false },
    { key: 'hardness_hb', label: 'Hardness', unit: 'HB', default: 220, live: true },
  ],
  motor: [
    { key: 'insulation_class', label: 'Winding insulation class', unit: '', default: 'F', options: ['B', 'F', 'H'], live: true },
    { key: 'rated_temp_rise_c', label: 'Rated temperature rise', unit: '°C', default: 15, live: true },
  ],
  brake: [
    { key: 'friction_class', label: 'Friction material class', unit: '', default: 'Organic/semi-metallic', live: false },
    { key: 'max_temp_c', label: 'Max operating temperature', unit: '°C', default: 300, live: true },
    { key: 'friction_coefficient', label: 'Coefficient of friction (μ)', unit: '', default: 0.4, live: true },
  ],
  rails: [
    { key: 'rail_profile', label: 'Rail profile/size', unit: '', default: 'T70', live: false },
    { key: 'straightness_tolerance', label: 'Straightness tolerance', unit: 'mm/5m', default: 1.0, live: false },
    { key: 'surface_hardness_hb', label: 'Surface hardness', unit: 'HB', default: 180, live: false },
  ],
  guide_shoes: [
    { key: 'roller_material', label: 'Roller/shoe material', unit: '', default: 'Polyurethane-tyred steel roller', live: false },
    { key: 'dynamic_rating_kn', label: 'Dynamic load rating', unit: 'kN', default: 20, live: true },
    { key: 'lubrication', label: 'Lubrication type', unit: '', default: 'Sealed / none (roller type)', live: false },
  ],
  counterweight: [
    { key: 'frame_grade', label: 'Frame material grade', unit: '', default: 'IS 2062 E250', live: false },
    { key: 'load_margin_pct', label: 'Design load margin', unit: '%', default: 10, live: false },
  ],
  rope_end: [
    { key: 'termination_type', label: 'Termination type', unit: '', default: 'Babbitt', options: ['Babbitt', 'Wedge', 'Ferrule'], live: false },
    { key: 'rated_holding_load_kn', label: 'Rated holding load', unit: 'kN', default: 60, live: true },
  ],
}

/** Which extra fields on a component actually drive health/P30d/RUL today. */
export function liveFieldKeys(type) {
  return (EXTRA_FIELDS[type] || []).filter((f) => f.live).map((f) => f.key)
}
export function decorativeFieldKeys(type) {
  return (EXTRA_FIELDS[type] || []).filter((f) => !f.live).map((f) => f.key)
}

// ---------------------------------------------------------------------------
// Presets - 3-5 industry-typical materials per component. Rope, sheave and
// guide-shoe (bearing-like) presets reuse Elev Care's own ml/elevcare_ml
// values; the rest (belt, motor, brake, rails, counterweight, rope end) are
// new presets authored for this wizard, at comparable realism/detail.
// ---------------------------------------------------------------------------

export const PRESETS = {
  rope: [
    { id: '8x19S-FC', material_type: 'Steel wire rope 8x19 Seale + fibre core', material_grade: '1570/1770 N/mm² (EN 12385-5)', tensile_grade: '1570/1770',
      composition: 'High-carbon steel wire (0.6-0.9% C), fibre core', strength: 'MBL 60 kN per rope (10 mm)', coating: 'Bright, lubricated',
      thickness_mm: 10, weight: '0.38 kg/m', breaking_load_kn: 60, rope_diameter_mm: 10 },
    { id: '6x19S-FC', material_type: 'Steel wire rope 6x19 Seale + fibre core', material_grade: '1370/1770 N/mm² (EN 12385-5)', tensile_grade: '1370/1770',
      composition: 'High-carbon steel wire, fibre core', strength: 'MBL 55 kN per rope (10 mm)', coating: 'Bright, lubricated',
      thickness_mm: 10, weight: '0.37 kg/m', breaking_load_kn: 55, rope_diameter_mm: 10 },
    { id: '8x19S-IWRC', material_type: 'Steel wire rope 8x19 Seale + steel core (IWRC)', material_grade: '1770/1960 N/mm² (EN 12385-5)', tensile_grade: '1770/1960',
      composition: 'High-carbon steel wire, independent wire-rope core', strength: 'MBL 72 kN per rope (10 mm)', coating: 'Zinc-galvanised, lubricated',
      thickness_mm: 10, weight: '0.44 kg/m', breaking_load_kn: 72, rope_diameter_mm: 10 },
    { id: 'CF-rope', material_type: 'Carbon-fibre core rope with friction coating', material_grade: 'CFRP core, high-friction jacket', tensile_grade: 'n/a (CFRP)',
      composition: 'Carbon-fibre core, polymer friction coating', strength: 'MBL 80 kN per rope', coating: 'High-friction polymer',
      thickness_mm: 8, weight: '0.12 kg/m', gsm: 1800, breaking_load_kn: 80, rope_diameter_mm: 8 },
  ],
  belt: [
    { id: 'PU-belt', material_type: 'PU-coated flat steel belt', material_grade: 'Steel cord 1770 N/mm² in PU jacket', tensile_grade: '1770',
      composition: 'Zinc-coated steel cords in polyurethane', strength: 'MBL 90 kN per belt', coating: 'Polyurethane jacket',
      thickness_mm: 3, weight: '0.30 kg/m', gsm: 2400, breaking_load_kn: 90, belt_dims: '30 x 3' },
    { id: 'PU-belt-wide', material_type: 'PU-coated flat steel belt (wide)', material_grade: 'Steel cord 1960 N/mm² in PU jacket', tensile_grade: '1960',
      composition: 'Zinc-coated steel cords in polyurethane', strength: 'MBL 120 kN per belt', coating: 'Polyurethane jacket',
      thickness_mm: 4, weight: '0.42 kg/m', gsm: 2800, breaking_load_kn: 120, belt_dims: '40 x 4' },
    { id: 'aramid-belt', material_type: 'Aramid-cord flat belt', material_grade: 'Aramid cord in polyurethane', tensile_grade: '2000 (aramid)',
      composition: 'Aramid fibre cords, PU jacket', strength: 'MBL 75 kN per belt', coating: 'Polyurethane jacket',
      thickness_mm: 3, weight: '0.20 kg/m', gsm: 2100, breaking_load_kn: 75, belt_dims: '30 x 3' },
  ],
  sheave: [
    { id: 'GJL-250', material_type: 'Grey cast iron EN-GJL-250 (GG25)', material_grade: 'EN-GJL-250', composition: 'Grey cast iron, flake graphite',
      strength: '250 MPa tensile, ~220 HB', coating: 'None', thickness_mm: 60, weight: '185 kg', hardness_hb: 220 },
    { id: 'GJS-500-7', material_type: 'Ductile iron EN-GJS-500-7', material_grade: 'EN-GJS-500-7', composition: 'Spheroidal-graphite iron',
      strength: '500 MPa tensile, ~190 HB', coating: 'None', thickness_mm: 55, weight: '180 kg', hardness_hb: 190 },
    { id: 'GJS-600-3', material_type: 'Ductile iron EN-GJS-600-3', material_grade: 'EN-GJS-600-3', composition: 'Spheroidal-graphite iron, pearlitic',
      strength: '600 MPa tensile, ~230 HB', coating: 'Induction-hardened grooves', thickness_mm: 55, weight: '180 kg', hardness_hb: 230 },
    { id: 'C45', material_type: 'Forged steel C45', material_grade: 'C45 (1.0503)', composition: 'Medium-carbon steel, 0.45% C',
      strength: '600 MPa tensile, ~210 HB (grooves 50 HRC)', coating: 'Induction-hardened grooves', thickness_mm: 50, weight: '165 kg', hardness_hb: 210 },
    { id: 'PU-liner', material_type: 'PU groove liner on cast iron sheave', material_grade: 'Polyurethane 92 Shore A', composition: 'Cast polyurethane liner, cast-iron body',
      strength: '35 MPa tensile (liner)', coating: 'PU liner', thickness_mm: 12, weight: '190 kg', gsm: 3200, hardness_hb: 25 },
  ],
  motor: [
    { id: 'induction-F', material_type: 'Copper-wound induction motor, insulation class F', material_grade: 'Class F (155°C)', composition: 'Electrolytic copper windings, silicon-steel laminations',
      strength: 'Rated rise 15°C over class F limit', coating: 'VPI epoxy varnish', thickness_mm: 1.2, weight: '165 kg', insulation_class: 'F', rated_temp_rise_c: 15 },
    { id: 'induction-B', material_type: 'Copper-wound induction motor, insulation class B (older machines)', material_grade: 'Class B (130°C)', composition: 'Electrolytic copper windings, silicon-steel laminations',
      strength: 'Rated rise 20°C over class B limit', coating: 'Varnish', thickness_mm: 1.2, weight: '170 kg', insulation_class: 'B', rated_temp_rise_c: 20 },
    { id: 'pm-gearless-H', material_type: 'PM synchronous gearless motor, insulation class H', material_grade: 'Class H (180°C)', composition: 'NdFeB magnets, copper windings',
      strength: 'Rated rise 12°C over class H limit', coating: 'VPI epoxy varnish', thickness_mm: 1.0, weight: '210 kg', insulation_class: 'H', rated_temp_rise_c: 12 },
  ],
  brake: [
    { id: 'organic', material_type: 'Organic/semi-metallic friction pads (asbestos-free)', material_grade: 'Organic, μ = 0.40', composition: 'Aramid fibre, resin binder, metallic fillers',
      strength: 'Max 300°C', coating: 'None', thickness_mm: 8, weight: '1.2 kg', gsm: 4200, friction_class: 'Organic/semi-metallic', max_temp_c: 300, friction_coefficient: 0.4 },
    { id: 'sintered', material_type: 'Sintered metallic brake pads (high-temp)', material_grade: 'Sintered, μ = 0.35', composition: 'Iron/copper sinter with ceramic fillers',
      strength: 'Max 450°C', coating: 'None', thickness_mm: 10, weight: '1.6 kg', friction_class: 'Sintered metallic', max_temp_c: 450, friction_coefficient: 0.35 },
    { id: 'ceramic', material_type: 'Ceramic-composite brake pads', material_grade: 'Ceramic composite, μ = 0.42', composition: 'Ceramic fibre matrix, resin binder',
      strength: 'Max 380°C', coating: 'None', thickness_mm: 9, weight: '1.1 kg', friction_class: 'Ceramic composite', max_temp_c: 380, friction_coefficient: 0.42 },
  ],
  rails: [
    { id: 'T70', material_type: 'Cold-drawn carbon steel T-rail (T70)', material_grade: 'T70 / IS 6216', composition: 'Cold-drawn carbon steel',
      strength: '370 MPa tensile', coating: 'Anti-corrosion oil', thickness_mm: 9, weight: '5.2 kg/m', rail_profile: 'T70', straightness_tolerance: 1.0, surface_hardness_hb: 180 },
    { id: 'T89', material_type: 'Machined carbon steel T-rail (T89, high-speed)', material_grade: 'T89 / IS 6216', composition: 'Machined carbon steel',
      strength: '420 MPa tensile', coating: 'Anti-corrosion oil', thickness_mm: 11, weight: '8.3 kg/m', rail_profile: 'T89', straightness_tolerance: 0.5, surface_hardness_hb: 200 },
    { id: 'ss-clad', material_type: 'Stainless-clad guide rail (corrosive environments)', material_grade: 'T70 core, 304 SS cladding', composition: 'Carbon steel core, stainless cladding',
      strength: '370 MPa tensile (core)', coating: 'Stainless cladding', thickness_mm: 9, weight: '5.6 kg/m', rail_profile: 'T70 (clad)', straightness_tolerance: 1.0, surface_hardness_hb: 190 },
  ],
  guide_shoes: [
    { id: 'SRB', material_type: 'Spherical roller bearing (guide roller)', material_grade: '100Cr6 / GCr15, 58-65 HRC', composition: 'Chromium bearing steel, brass cage',
      strength: 'C = 300 kN, C0 = 420 kN', coating: 'Grease, seals', thickness_mm: 22, weight: '9.5 kg', dynamic_rating_kn: 300, roller_material: 'Chromium bearing steel roller' },
    { id: 'DGBB', material_type: 'Deep-groove ball bearing (guide roller)', material_grade: '100Cr6, 58-65 HRC', composition: 'Chromium bearing steel, polyamide cage',
      strength: 'C = 160 kN, C0 = 190 kN', coating: 'Grease, shields', thickness_mm: 18, weight: '5.8 kg', dynamic_rating_kn: 160, roller_material: 'Chromium bearing steel roller' },
    { id: 'PU-tyre', material_type: 'Polyurethane-tyred steel roller', material_grade: 'PU 92 Shore A on steel hub', composition: 'Cast polyurethane tyre, steel hub/bearing',
      strength: 'Dynamic rating 20 kN', coating: 'PU tyre', thickness_mm: 15, weight: '2.1 kg', gsm: 3000, dynamic_rating_kn: 20, roller_material: 'PU-tyred steel roller' },
  ],
  counterweight: [
    { id: 'welded-mild', material_type: 'Welded mild-steel frame', material_grade: 'IS 2062 E250', composition: 'Mild steel channel/plate, welded',
      strength: '250 MPa yield', coating: 'Anti-corrosion paint', thickness_mm: 8, weight: '620 kg', frame_grade: 'IS 2062 E250', load_margin_pct: 10 },
    { id: 'bolted-structural', material_type: 'Bolted structural-steel frame (high load margin)', material_grade: 'IS 2062 E350', composition: 'Structural steel sections, bolted',
      strength: '350 MPa yield', coating: 'Hot-dip galvanised', thickness_mm: 10, weight: '680 kg', frame_grade: 'IS 2062 E350', load_margin_pct: 20 },
    { id: 'cast-iron-filler', material_type: 'Cast-iron filler block frame (older design)', material_grade: 'EN-GJL-200', composition: 'Cast-iron filler blocks in steel frame',
      strength: '200 MPa tensile', coating: 'None', thickness_mm: 8, weight: '710 kg', frame_grade: 'EN-GJL-200', load_margin_pct: 5 },
  ],
  rope_end: [
    { id: 'babbitt', material_type: 'Babbitt-metal socket termination', material_grade: 'Babbitt (tin-based)', composition: 'Poured tin-based Babbitt in forged socket',
      strength: 'Rated holding 60 kN', coating: 'None', thickness_mm: 40, weight: '1.8 kg', termination_type: 'Babbitt', rated_holding_load_kn: 60 },
    { id: 'wedge', material_type: 'Wedge-and-socket termination', material_grade: 'Forged steel wedge + socket', composition: 'Forged carbon steel',
      strength: 'Rated holding 55 kN', coating: 'Zinc-plated', thickness_mm: 40, weight: '1.5 kg', termination_type: 'Wedge', rated_holding_load_kn: 55 },
    { id: 'ferrule', material_type: 'Swaged ferrule termination', material_grade: 'Aluminium/copper ferrule', composition: 'Swaged metal ferrule over rope end',
      strength: 'Rated holding 50 kN', coating: 'None', thickness_mm: 25, weight: '0.6 kg', termination_type: 'Ferrule', rated_holding_load_kn: 50 },
  ],
}

export function applyPreset(type, presetId) {
  const p = (PRESETS[type] || []).find((x) => x.id === presetId)
  if (!p) return null
  const { id, ...vals } = p
  if (vals.gsm === undefined) vals.gsm = gsmApplicable(type, vals.material_type) ? '' : 'Not applicable'
  return { ...vals, preset: presetId }
}

// ---------------------------------------------------------------------------
// Completeness & provenance (mirrors materials.py resolve()/completeness())
// ---------------------------------------------------------------------------

function numOrNull(v) {
  if (v === null || v === undefined || v === '' || v === 'Not applicable') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

/** All applicable field keys for a component, GSM excluded when not applicable. */
export function applicableKeys(type, spec) {
  const gsmOk = gsmApplicable(type, spec?.material_type)
  const keys = [...FIELD_KEYS, ...(EXTRA_FIELDS[type] || []).map((f) => f.key)]
  return keys.filter((k) => k !== 'gsm' || gsmOk)
}

export function completeness(type, spec) {
  spec = spec || {}
  const keys = applicableKeys(type, spec)
  if (!keys.length) return 0
  const filled = keys.filter((k) => spec[k] !== null && spec[k] !== undefined && spec[k] !== '').length
  return Math.round((100 * filled) / keys.length)
}

/**
 * Physics-style inputs with provenance, one row per LIVE field (the ones that
 * actually feed computeMetrics). Decorative fields are listed separately so
 * the Elevator summary page can show both without implying decorative fields
 * affect the prediction.
 */
export function resolveInputs(type, spec) {
  spec = spec || {}
  const extras = EXTRA_FIELDS[type] || []
  const live = []
  const decorative = []
  extras.forEach((f) => {
    const userVal = spec[f.key]
    const hasUser = userVal !== null && userVal !== undefined && userVal !== ''
    const row = { key: f.key, label: f.label, unit: f.unit, value: hasUser ? userVal : f.default, source: hasUser ? 'user' : 'default', default: f.default }
    ;(f.live ? live : decorative).push(row)
  })
  return { live, decorative }
}

// ---------------------------------------------------------------------------
// Simplified health / P30d / RUL calculation
//
// SCENARIO_BASE gives each of the three hand-picked demo scenarios a starting
// point; a component's live material-spec fields then nudge that baseline up
// or down relative to the documented default for each field (a stronger rope,
// a higher dynamic load rating, etc. reads as healthier). This is a
// transparent stand-in for the trained ML model used elsewhere in the app -
// good enough to demo the wizard end to end, not a substitute for it.
// ---------------------------------------------------------------------------

export const SCENARIO_BASE = {
  good: { health: 88, p30d: 4, rul_days: 500 },
  medium: { health: 58, p30d: 22, rul_days: 90 },
  risk: { health: 26, p30d: 55, rul_days: 20 },
}

const RUL_CAP_DAYS = 3650 // "> 365 d" is shown in the UI once RUL crosses 365

export function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)) }

export function statusFromHealth(health) {
  if (health >= 70) return 'good'
  if (health >= 40) return 'medium'
  return 'critical'
}

/** Health/P30d/RUL for one component. `spec` may be null/undefined/partial - every field falls back to its documented default, never to zero or a crash. */
export function computeComponent(type, scenario, spec) {
  const base = SCENARIO_BASE[scenario] || SCENARIO_BASE.medium
  const extras = EXTRA_FIELDS[type] || []
  const liveExtras = extras.filter((f) => f.live && typeof f.default === 'number')
  let qualitySum = 0
  let qualityCount = 0
  liveExtras.forEach((f) => {
    const val = numOrNull(spec?.[f.key])
    if (val == null || !f.default) return
    qualitySum += clamp(val / f.default, 0.6, 1.4)
    qualityCount += 1
  })
  const qualityFactor = qualityCount ? qualitySum / qualityCount : 1 // 1 = nominal/default spec
  const healthDelta = (qualityFactor - 1) * 22
  const health = Math.round(clamp(base.health + healthDelta, 1, 99))
  const p30d = Math.round(clamp(base.p30d - healthDelta * 0.9, 0.5, 97) * 10) / 10
  const rul_days = Math.min(RUL_CAP_DAYS, Math.max(1, Math.round(base.rul_days * qualityFactor)))
  return { health, p30d, rul_days, status: statusFromHealth(health) }
}

export function formatRul(days) {
  if (days == null) return '-'
  return days > 365 ? '> 365 d' : `${days} d`
}

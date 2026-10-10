# Round 5 (24 Sep 2026) — traction only, material specs, rope fault report, sensor streaming

## 1. Traction lift only
- Removed the Round-4 elevator-type / hydraulic flow entirely (frontend engine, type dashboard, component grid, analysis and summary pages, chat hook, styles). No hydraulic reference remains.
- `lift_type = "traction"` is a fixed value on every elevator document and in `elevator_view`; shown as a read-only badge on cards, the elevator header, the report and the new pages.
- Optional `drive_type` (Gearless PM / Geared) via `PATCH /elevators/{id}`; it only changes the default part rows offered in the traction-machine material form.

## 2. Material specification per component
- `ml/elevcare_ml/materials.py`: the eight fields (Material type, grade, GSM, thickness, composition, weight, strength, coating) with units, industry presets for rope / sheave / bearing / machine parts, extra fields (rope count 4-8, rope diameter, breaking load, tensile grade; groove type, sheave and groove diameter, hardness; C / C0, cage, lubrication; insulation class), the GSM rule (auto "Not applicable" for steel wire, cast and forged parts), completeness %, blank-field detection and `resolve()` -> physics parameters with `user` / `default` provenance.
- Physics laws now accept `params`: rope S_u and D/d (Feyrer), sheave hardness HB x 9.807e6 (Archard), bearing dynamic rating C (Lundberg-Palmgren), insulation class -> winding thermal limit / rated rise (Arrhenius). Blank fields fall back to the constants in `physics.py`.
- API: `GET/PUT /components/{id}/material-spec`, `POST /components/{id}/material-spec/preset/{id}`, `GET /elevators/{id}/material-spec`. Saving stores `material_spec` + `physics_params` on the component, feeds the live ingestion accumulator, and triggers a fresh prediction (`IngestionPipeline.repredict`).
- UI: `/elevators/:id/materials` first-time setup page and a "Materials & specification" section on every component page; the Model card lists every physics input with its source.

## 3. Lift report - suspension rope fault section
- `ml/elevcare_ml/rope_report.py` (pure numpy) + `backend/app/services/rope_report.py`: per-rope 24 h median, deviation, 7- and 30-day trends, status (Healthy < 5 %, Watch 5-10 % or trending up, Faulty >= 10 % or over the limit for 3 readings) read from `physics.THRESHOLDS["rope"]["rope_deviation_pct"]`; wear multiplier (1 + dev)^b with b = 4.5; days-to-limit from the trend; ranked factors with layman meanings; hedged causes; recommendation (re-tension / replace rope Rn only / replace whole set) with urgency days and next checkup; 30-day series.
- `GET /elevators/{id}/reports/suspension-rope`; page `/elevators/:id/report` with print CSS (Print / Save as PDF), banner, table + bar chart with set-average line and alarm band, sparkline grid, "Ask the assistant". The chatbot receives the same `rope_brief()` data, so report and assistant agree. Demo elevator elv-03 flags rope **R6**.

## 4. Sensor input streaming
- Collection `sensors` + in-memory `SensorHub` (`services/sensor_hub.py`): registry, online / stale / offline from last_seen vs expected interval, message rate, per-sensor ring buffer, per-component 10-second buckets that assemble the standard `SensorReading` and hand it to the unchanged `IngestionPipeline`; unknown sensor IDs are rejected and listed; coverage checklist per component with missing-sensor warnings; source selector (Simulated / External gateway).
- API: `GET /sensors/meta`, `GET /elevators/{id}/sensors`, `PUT/DELETE /elevators/{id}/sensors/{sid}`, `POST .../sensors/defaults`, `GET .../sensors/live`, `GET .../sensors/{sid}/series` (rolling chart, waveform + FFT), `PUT .../sensors/source`, `POST .../sensors/{sid}/test`, `GET .../sensors/{sid}/sample` (JSON + curl), `POST /ingest/sensor-data` (X-Ingest-Key), `WS /ws/ingest/{elevator_id}?key=`.
- The simulator auto-registers the standard sensor set per elevator and mirrors its readings onto those sensors, so the page is populated in both scenarios.
- Waveform length fix: `signal.to_frame` resamples any 32-4096-sample trace to 256 before feature extraction (tested at 64 / 256 / 512).

## Tests
- backend: 58 (5 new acceptance tests: no hydraulic + badge, spec round-trip + physics change + model-card provenance, demo report names R6 + chatbot agrees, sensor registry/live/ingest/prediction update, WebSocket ingest).
- ml: 27 (7 new: presets, resolve/provenance, completeness, rope analysis, frame lengths).
- frontend: 5 render tests against real API fixtures (`npm test`); `npm run build` passes.

# Elev Care — review & improvement notes (23 Sep 2026)

## Bugs fixed
- **Chatbot could return HTTP 500.** The Claude call used speculative SDK parameters and only caught API-typed errors; a `TypeError` from the SDK propagated. Now any failure falls back to the rule engine (`chatbot.claude_answer`). Model / max tokens are config (`CHATBOT_MODEL`, `CHATBOT_MAX_TOKENS`).
- Chatbot intent routing: `"last"` matched "last inspection" and sent it to the RUL branch; `None` scores crashed f-strings (`_n()` guard).
- Sheave/pulley had no wear law at all; the first Archard constants gave 1.1 m of groove wear at rated life — now calibrated to the 1.5 mm regroove limit (test-enforced).
- `ingestion._window` re-load did not carry per-reading life fraction (needed for the physics RUL rate).

## Physics — one approved law per component (`ml/elevcare_ml/physics.py`)
- `rope_cycles_to_failure` — Feyrer `N_f = C (D/d)^a (S_u/S)^b`
- `sheave_wear_depth` / `sheave_wear_increment` — Archard `Q = k W L / H` with contact-pressure feedback (spec 2.2)
- `bearing_l10_life` / `bearing_l10_increment` — Lundberg-Palmgren `L10 = (C/P)^p`, p = 10/3
- `traction_damage_increment` — Palmgren-Miner + Arrhenius (rate ×2 per +10 °C)
- `life_increment()` dispatches; used identically by simulator, training and live ingestion. Each law calibrated so N(nominal) = rated cycles.

## Rule layer (`ml/elevcare_ml/rules.py`, applied in `predictor._compose`)
R1 alarm-limit override · R2 rated life consumed · R3 active growth · R4 fast response (last 3 raw readings) · R5 RUL/health consistency + physics-RUL cap · R6 unconfirmed-ML hold · R7 hysteresis (3 confirmations + 15 % dead-band) · R8 low confidence. Fired rules are stored on the component, in predictions, alerts, the API, the UI and the assistant. On the seeded fleet, boundary flapping dropped from 14 changes / 60 days to 0 on the worst component.

## Speed
- `features.window_features_batch`: vectorised training windows (bit-identical to the live path; test-enforced).
- `signal.waveform_features`: 4× faster (large array ops, no per-row work).
- `train_all(n_jobs=-1)`: component types trained in parallel. ~50 s → 27 s on 1 core, ~8 s on 4.
- Ingestion: one batched model call per component type instead of one per component.

## Waveform analysis (`signal.analyze_waveform`)
Time stats, FFT peaks, Hilbert envelope spectrum, characteristic-frequency table (BPFO, groove-pass, 2× line, sway) with SNR and *visible* vs *elevated*, band energy, diagnosis. API: `GET /components/{id}/waveform` (adds `analysis`, `envelope_spectrum`), `POST /components/{id}/waveform/analyze`. UI: new *Waveform analysis* card and *Verdict* (rules) card on the component page; model card lists the laws and rules.

## Assistant
Situation reports ("what is happening with …", "how fast is … decaying", "what does the waveform show"): status, 7/30-day health change, decay rate and projected days to at-risk, life used and its law, rules fired, waveform findings, trips/day and energy vs healthy baseline, complaints, next action. Same data is given to Claude when a key is set.

## Tests
ML: 8 → 18 tests (laws, batch equivalence, each rule, envelope/BPFO). Backend: 17 pass. Frontend builds.

---

# Round 2 — fix pack (24 Sep 2026)

Seven-section fix/feature pack. Nothing removed: existing pages, login flow, physics
laws, rule layer and ML pipeline are untouched except where a section below names them.

## Section 1 — energy values increasing on every click (root cause + fix)
Root cause: the old `/analytics/energy` endpoint aggregated the simulator's
ever-growing `energy_logs` collection over a rolling window, so every click
(which triggered a refetch) saw a larger total as the simulator kept appending
rows in the background — nothing was actually re-randomising per click, but the
symptom looked identical.

Fix: new `backend/app/services/opdata.py` — a deterministic generator seeded on
`md5(elevator_id, calendar_period)` (not Python's salted `hash()`, which would
break across restarts). New endpoint `GET /analytics/energy/calendar?view=month|batch`.
Month view = last 12 months ending at the current month-to-date; batch view =
today's four 6-hour windows. Completed periods are byte-identical across calls
and across restarts; only the current period grows, prorated by the real clock.
Cost is always `kwh × tariff`, read from the same stored kWh. Rewrote
`frontend/src/pages/Energy.jsx` to read from it (old rolling-window trend kept
as a third "Daily trend" view for the savings-analysis panel).

## Section 2 — no back button
New `frontend/src/components/BackBar.jsx`: a back button that goes to the
logical parent (not raw browser history, so a notification deep-link doesn't
strand you) plus clickable breadcrumbs. Added to every page except `/login`.
Filters (history range, energy view, cycle period, error severity) moved into
the URL via `useStickyParam`, so back/forward/refresh restore the same view.

## Section 3 — KONE-style operational data
New `backend/app/services/opdata.py` (`operational_summary`, `error_events`,
deterministic per elevator+date) + `backend/app/routers/operations.py`
(`GET /elevators/{id}/operational`). New page
`frontend/src/pages/Operational.jsx`: seven inputs as cards + trend charts,
sortable/filterable error-code table, connected-systems performance, and a
"why is this at risk?" panel driven by `operational_risk_uplift()` (bounded
+12pp added to failure probability — feeds Sections 5/6, never dominates the
physics+ML prediction).

## Section 4 — usage cycle counts
`opdata.cycle_series/lifetime_cycles/cycle_totals/monthly_table`. New page
`frontend/src/pages/Cycles.jsx`: week/month/year toggle, lifetime-vs-rated
progress bar, lift comparison, Jan–Dec exportable table (CSV, both server-side
`GET /analytics/cycles/monthly.csv` and a client-side export button). Cycle
definitions are explicit per type (tooltip on each card) and every component's
count is derived from the lift's own trips/door-movements, so a component can
never out-cycle its lift (test-enforced).

## Section 5 — one shared risk model everywhere
`backend/app/services/risk.py` / `frontend/src/risk.js`: four levels
(critical/high/medium/good) from failure probability OR remaining life,
whichever is worse. `services/views.py` now annotates every component and
attaches a fleet-wide roll-up (`elevator.risk.counts`, `.banner`). Applied to:
table rows (tinted + badge + REPLACE tag + "show only at-risk" filter, sorted
critical-first), the failure-probability chart (labelled threshold lines +
crossing marker), component cards (pulsing dot on critical), and a
click-to-scroll banner on the elevator page. Colour is never the only signal —
every element pairs it with an icon or text label.

## Section 6 — structured recommendations + component-vs-unit decision
`backend/app/services/recommendations.py`: `decide_action()` implements the
four ordered rules (past design life → coupled degradation → repair cost
> 60% of unit replacement → isolated part → lighter action), returns the rule
name and a plain-language reason. New `RecommendationCard`
(`frontend/src/components/risk.jsx`) used identically on the elevator page,
`/recommendations` (new fleet-wide page) and the chatbot's replies
(`chatbot.py` renders the same card shape as text). New endpoints
`GET /elevators/{id}/recommendations`, `GET /analytics/recommendations`.

Fixed during testing: rules 2–4 were evaluating a component's *neighbours*
without first checking that the component itself was degraded, so a perfectly
healthy traction machine could be told "REPLACE WHOLE UNIT" purely because a
coupled rope was critical. Rules 2–4 now only fire on a component that is
itself critical/high; rule 1 (design-life) still fires independently, since
that's a legitimate preventive case.

## Section 7 — consistency
Every new figure (energy, operational data, cycles, risk, recommendations)
reads from the same `opdata`/`risk`/`recommendations` services regardless of
which page asks, so the dashboard, the tables and the chatbot always agree.
`useLiveFetch` now no-ops on a null path instead of firing a malformed
request. All new endpoints return typed errors and empty-state-friendly shapes.

## Tests
Backend: 17 → 53 (36 new, covering determinism, frozen history, cycle
consistency, the four decision rules including the regression above, and the
new endpoints end-to-end). ML: unchanged, 18 pass. Frontend: builds clean;
static import/JSX-reference check run across every changed file.

---

# Round 3 — startup crash fix (24 Sep 2026)

## Bug: app fails to start with `ModuleNotFoundError: No module named '_loss'`
Pre-existing issue, not introduced by Rounds 1-2 (the affected file,
`ml/elevcare_ml/predictor.py`, was untouched until now). `Predictor.__init__`
called `joblib.load()` on the committed `ml/artifacts/models.joblib` with no
exception handling at all. A `.joblib` file is a pickle of scikit-learn's own
objects, including private internals like `HistGradientBoosting`'s loss
classes - unpickling is only guaranteed to work with the *exact* scikit-learn
version that created the file. `requirements.txt` allowed a wide range
(`scikit-learn>=1.5,<1.10`), so a fresh `pip install` on a different machine
can resolve a version whose internal `_loss` module layout differs from the
one used to build the committed artifact, and the whole FastAPI app crashed
on startup with no fallback.

**Fix** (`ml/elevcare_ml/predictor.py`): `joblib.load()` is now wrapped in a
try/except. Any failure - a version mismatch, a corrupted download, a binary
file mangled by git - logs a clear warning naming the cause and retrains a
fresh set of models on the spot (same code path as the pre-existing
missing-file case), rather than propagating the error. Training takes
20-30 seconds; a working artifact never triggers it (verified: startup with
the committed artifact is still ~3s, unchanged).

Reproduced the exact reported error with a crafted pickle
(`b"c_loss\nSomeLossClass\n."`, which raises the identical
`ModuleNotFoundError: No module named '_loss'`) and confirmed end-to-end: the
app now starts and serves traffic instead of crashing. Two new tests in
`ml/tests/test_ml.py` cover both the recovery path and that a caller who
explicitly disabled auto-training still gets a clear error instead of a silent
retrain.

## Tests
ML: 18 → 20. Backend: unchanged, 53 pass. Frontend: unchanged, builds clean.



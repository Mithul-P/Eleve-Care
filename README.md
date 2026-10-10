# Elev Care

**AI + physics-informed reliability prediction for elevator suspension systems** - ropes,
traction sheave, sheave bearings and the traction machine. Elev Care predicts **health score**,
**failure probability (30-day horizon)** and **remaining useful life (RUL)** from operational
sensor data. It then explains *why*, and turns that into cost, energy and sustainability numbers.

Built for the **KONE Elevate** challenge by team **Syntax Syndicate**.

> Elev Care is **decision support alongside technician inspection**, not a certified safety
> system. Buffers, safety gear, structural integrity, wiring insulation and hoistway conditions
> are deliberately out of scope and remain manual / technician-inspected.

---

## What's inside

| Area | Highlights |
|---|---|
| **Simulated data engine** | Physics-based degradation (Basquin/Miner fatigue + Paris-law acceleration), not random noise. **Batch** (6 h chunks) and **real-time** (every 3 s, WebSocket) modes with a toggle. Pluggable `SensorSource` → swap in a real feed without touching ML/API/UI. |
| **Hardcore demo elevator** | `Tower A - Lift 3 (Demo)`: a rope set with an abnormal, widening tension imbalance. Over the seeded 60 days it goes good → medium → **at-risk**, with a plain-English explanation and recommended action. |
| **ML layer (`/ml`)** | FFT and statistical vibration features (RMS, crest, kurtosis, defect-band energy) plus physics features (tension imbalance, current vs rated, temperature rise, Miner fatigue index). **Monotonic gradient-boosted trees** give P(failure) and RUL. The health score blends in physics severity, and explanations rank the contributing factors. |
| **Dashboard** | Fleet overview → elevator detail (components sorted at-risk → medium → good) → component detail. The component page has health and failure graphs (week/month), a condition-indicator trend with healthy/alarm limits, waveform + FFT spectrum, per-rope tension, and maintenance actions. |
| **Cost & energy** | Daily energy (healthy baseline vs excess from degradation) and cost at **₹5/unit**, per-elevator breakdown, and predictive-vs-reactive savings analysis. |
| **Sustainability** | Operational CO₂ (India grid factor), avoidable CO₂ from degradation, component life extension and avoided embodied carbon, circular-economy notes. |
| **AI assistant** | Answers health/RUL/checkup/complaint/energy questions grounded in live data and proactively raises near-failure and checkup-due alerts. Uses **Claude** when `ANTHROPIC_API_KEY` is set, otherwise a built-in rule engine (the demo never depends on a network). |
| **Complaints** | Public **no-login** form (`/report` or `/report/<elevator-id>`, e.g. a QR code in each car). Rate-limited. Complaints show up as live notifications in that elevator's admin view. |
| **Access control** | Elevator data is only visible to logged-in **admin/staff** (JWT). Admin-only: simulator mode switch, user creation. |

---

## Repository layout

```
/frontend            React (Vite) dashboard - dark, data-dense UI, Recharts
/backend             FastAPI + MongoDB (Motor)
  app/main.py        app factory & startup (model load, seed, simulator)
  app/routers/       auth, elevators, components, analytics, complaints, chatbot,
                     notifications, simulator, ingest, stream (WebSocket)
  app/services/      ingestion pipeline, simulated source, runner, analytics,
                     chatbot, notifications, energy model
  app/seed.py        demo fleet + 60 days of history (python -m app.seed --reset)
/ml                  physics, signal processing, simulator, features, training, predictor
  elevcare_ml/       importable package (no web dependencies)
docker-compose.yml   mongo + backend + frontend
.env.example         every setting, documented
```

The ML package has no knowledge of FastAPI or MongoDB. The backend imports it and calls
`Predictor.predict(...)`, so models can be retrained (`python -m elevcare_ml.train`) and
redeployed independently of the API.

---

## Quick start

### Option A - Docker (full stack)

```bash
cp .env.example .env          # set JWT_SECRET, SEED_ADMIN_PASSWORD, SEED_STAFF_PASSWORD
docker compose up --build
```

- Dashboard: http://localhost:8080 (sign in with the seed admin/staff credentials from `.env`)
- Public complaint form: http://localhost:8080/report
- API docs (OpenAPI): http://localhost:8000/docs

The backend image trains the ML models at build time (~1 min). On first start it seeds the
demo fleet and 60 days of history (~20 s).

### Option B - Local development

Prerequisites: Python 3.11+, Node 20+, and MongoDB (or none - see `mongomock://` below).

```bash
# backend (+ ML)
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp ../.env.example .env        # edit values; or export them
# no MongoDB installed? use the in-memory DB (data resets on restart):
#   MONGO_URL=mongomock://
uvicorn app.main:app --reload --port 8000
```

On first start the backend trains the models if `ml/artifacts/models.joblib` is missing (~45 s).
Run `python -m elevcare_ml.train` from `/ml` to retrain on demand.

```bash
# frontend (second terminal)
cd frontend
npm install
npm run dev                    # http://localhost:5173  (proxies /api and /ws to :8000)
```

If you leave `SEED_ADMIN_PASSWORD` / `SEED_STAFF_PASSWORD` empty, the backend generates random
passwords and prints them once in its log. No credentials are hard-coded.

### Tests

```bash
cd ml && pytest -q            # physics, signal processing, simulator, model monotonicity
cd backend && pytest -q       # API end to end on an in-memory DB (auth, complaints, ingest, chatbot, ...)
```

---

## Demo script (≈3 minutes)

1. **Fleet view**: one elevator is flagged **At risk** - *Tower A - Lift 3 (Demo)*.
2. Open it. **Suspension ropes** are at the top of the component list with the explanation:
   *rope tension imbalance ~19% (limit 10%) → the most-loaded rope wears exponentially faster
   (Basquin) → re-tension and inspect for broken wires*. Its two public complaints appear in
   the same view.
3. Open the rope component. Health and failure-probability graphs show the decline over
   30 days. The *Condition indicator* shows imbalance climbing through the alarm line. The
   per-rope tension chart shows rope R6 carrying +19%. The spectrum shows energy concentrated
   in the rope-sway band.
4. Open the **assistant** → *"Why is the demo lift's rope at risk?"*
5. Switch the header toggle to **Real-time**: waveforms and scores update every 3 s over
   WebSocket. Switch back to **Batch** and press **Run batch now** to ingest a 6-hour chunk.
6. **Record replacement** on the rope → it resets to healthy, and new readings confirm it.
7. **Cost & energy** and **Sustainability**: the rupees and CO₂ that degradation is costing,
   and what predictive maintenance saves. **Model card**: held-out metrics and feature
   importances.
8. Scan a "QR code" → `/report/elv-03` → submit a complaint → a toast appears live on the
   admin dashboard.

---

## The physics-informed model (for the judges)

**1. Degradation physics (simulator, `ml/elevcare_ml/simulator.py`).** Every component has a
hidden damage `D ∈ [0,1]`:

```
dD = trips · L^m / N_rated / life_scale · (1 + k·D)
      └── Basquin/Palmgren-Miner fatigue ──┘   └ Paris-law-style acceleration
```

`L` is the load factor. For ropes it is the **most-loaded rope**, `1 + imbalance`: that is why
tension imbalance is so damaging (with `m ≈ 4.5`, a rope carrying 20% extra ages ~2.3× faster).
Sensor readings are generated *from* `D`:
- widening rope-tension spread
- vibration signatures in each component's defect band (rope sway 4-16 Hz, sheave groove-pass
  20-60 Hz, bearing BPFO impacts ringing a 320 Hz resonance, 100 Hz electrical for the motor)
- creeping motor current and temperature

**2. Features (`features.py`, `signal.py`).**
- **Per reading:** RMS, peak, crest factor, kurtosis (≈3 healthy, ≫3 with bearing impacts), FFT
  defect-band energy ratio, tension imbalance and deviation, current ÷ rated, temperature rise.
- **Per component:** 24 h medians, 7-day trends, and a **Miner fatigue index** computed from
  *observed* trips × load, the same code in training and live.

**3. Models (`train.py`).** `HistGradientBoostingClassifier` (P(failure within 30 days)) and
`HistGradientBoostingRegressor` (log RUL), both with **monotonic constraints**: risk can only go
up, and RUL only down, as an indicator worsens. This is physics knowledge built into the ML.
Trained on simulated run-to-failure trajectories and evaluated **on held-out units**. See the
*Model card* page. Typical held-out ROC AUC is 0.84 (ropes) to 0.98 (bearings), and RUL MAE is
about 11-25 days in the last 90 days of life.

**4. Health and explanation (`predictor.py`).**
`health = 100 · (1 − ½·P_fail − ½·S_physics)`, where `S_physics` is the weighted severity of
each indicator between its *healthy* and *alarm* limits (`physics.THRESHOLDS`). The two-part
blend means an engineering limit is respected even before the model has seen a similar case.
Contributing factors are ranked by severity × (physics weight + ML permutation importance),
and each factor carries recommended-action advice.

| Status | Rule |
|---|---|
| at-risk | P_fail ≥ 50% or health < 45 or RUL < 30 d |
| medium | P_fail ≥ 20% or health < 72 or RUL < 120 d |
| good | otherwise |

Checkup interval follows status: 90 / 30 / 7 days.

---

## API overview

Interactive docs are at `/docs`. Main endpoints:

| Method | Path | Access |
|---|---|---|
| POST | `/auth/login` | public |
| GET | `/auth/me`, POST `/auth/users` | user / admin |
| GET | `/elevators`, `/elevators/{id}`, `/elevators/{id}/components` | staff |
| GET | `/components/{id}`, `/components/{id}/history?range=week\|month`, `/components/{id}/prediction`, `/components/{id}/waveform` | staff |
| POST | `/components/{id}/maintenance` (`inspection` \| `replacement`) | staff |
| GET | `/elevators/{id}/energy`, `/analytics/energy`, `/analytics/sustainability`, `/analytics/model` | staff |
| POST | `/complaints` | **public** (rate-limited) |
| GET | `/public/elevators` (id, name, building only) | **public** |
| GET/PATCH | `/complaints`, `/complaints/{id}` | staff |
| POST | `/chatbot/query`, GET `/chatbot/notifications` | staff |
| GET | `/notifications` | staff |
| GET | `/simulator/status` | staff |
| POST | `/simulator/toggle-mode`, `/simulator/run-batch` | admin |
| POST | `/ingest/readings` (header `X-Ingest-Key`) | sensor gateway |
| WS | `/ws/stream?token=<JWT>` | staff - live readings, predictions, alerts, complaints |

### Plugging in real sensors

Real devices only need to produce the `SensorReading` contract
(`backend/app/services/ingestion.py`):

```json
{"component_id": "elv-01-rope", "timestamp": "2026-09-23T10:00:00Z", "tension": 8.6,
 "rope_tensions": [8.5, 8.7, 8.6, 8.4, 8.8, 8.6], "vibration_waveform": [/* ≥32 samples, g @1 kHz */],
 "motor_current": 23.4, "temperature": 39.5, "cycle_count": 1234567}
```

Either `POST /ingest/readings` from a gateway (set `INGEST_API_KEY`), or implement another
source class next to `SimulatedSource` (e.g. MQTT). Set `SIM_ENABLED=false` once real data
flows. Features, predictions, energy, alerts and the UI need no changes.

### Data model (MongoDB)

- `elevators`
- `components`: current `health_score`, `failure_probability`, `rul_days`, `status`,
  `contributing_factors`, `fatigue_index`
- `sensor_readings`: raw fields + computed `features`
- `predictions`: `model_version`, `health_score`, `failure_probability`, `RUL_days`,
  `contributing_factors`
- `complaints`: `submitted_without_login`
- `energy_logs`: daily `energy_kwh`, `baseline_kwh`, `cost` at ₹5/unit, `trip_count`
- `users`: role `admin` | `staff`, PBKDF2 password hash
- Supporting collections: `alerts`, `maintenance_logs`, `simulator_state`, `simulator_config`

---

## Deploying later

Nothing here is tied to localhost:

- **Backend** (Render / Railway / Fly.io): deploy `backend/Dockerfile` with the **repository
  root** as build context. It honours `$PORT`. Set `MONGO_URL` (MongoDB Atlas SRV string),
  `JWT_SECRET`, the seed passwords, `CORS_ORIGINS=https://<your-frontend-domain>`, and
  optionally `ANTHROPIC_API_KEY` / `INGEST_API_KEY`.
- **Database**: MongoDB Atlas. Indexes are created automatically on startup, and seeding runs
  once on an empty DB (set `SEED_ON_STARTUP=false` for production data).
- **Frontend** (Vercel): project root `frontend/`, build `npm run build`, output `dist/`.
  `vercel.json` handles SPA routing. Set `VITE_API_BASE=https://<backend-host>` and
  `VITE_WS_URL=wss://<backend-host>/ws/stream`.
- **Real-time at scale**: the WebSocket broadcaster is in-process. Run a single backend
  instance, or add Redis pub/sub before scaling out horizontally.
- **Batch cadence**: `SIM_BATCH_INTERVAL_SECONDS=21600` gives real 6-hour chunks. Lower it for
  demos, or use the **Run batch now** button.

## Configuration

See [`.env.example`](.env.example). Every setting is an environment variable.

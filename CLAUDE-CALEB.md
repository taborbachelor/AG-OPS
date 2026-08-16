# CLAUDE-CALEB

## User
- Name: Caleb (Tabor Bachelor)
- GitHub: taborbachelor
- Email: taborbachelor@gmail.com

## Projects

### Relevyn
- Status: In progress
- Location: See **CLAUDE-RELEVYN.md** (C:\Users\tabor\CLAUDE-RELEVYN.md) — full context lives there
- Description: AI brand-visibility SaaS + X/social marketing automation — completely separate from RC Plane project

### RC Plane GCS (Friend's Project)
- **VISION:** an autonomous **field-spraying drone business**. Customer website (order → select field → schedule → pay via Stripe → confirmed + updates) + auto field selection w/ boundary snap + auto coverage flight paths + terrain intelligence (field/tree/water recognition & avoidance). GCS = operator side, website = customer side. Reality flags given to user: needs Stripe account, and commercial spraying = FAA Part 137 + chemical licensing (their homework, not blocking software).
- Status: **Full ag-ops platform + directive milestones M1–M3 shipped, all SITL-validated** (last commit `256bbed`, 2026-07-24). Ag platform: GCS + customer website + zone-aware multi-field job planner + USDA imagery field auto-detection + 20-defect refinement audit. Directive M1–M3: observability/event-log, verified param writes, link identity (sysid/state-machine/RX-filter), parameter engine (cache/sync/validation/atomic). **All 4 hardware-blocking findings cleared.** Remaining work is hardware-gated (Cube bench test, Stripe keys, powerline data), roadmap M4+, or new ideas.
- Location: C:\Users\tabor\rc-plane-app (custom app)
- GitHub: https://github.com/taborbachelor/caleb-rc-project (branch `main`) — THIS is the project repo
- Reference: C:\Users\tabor\rc-plane (Mission Planner fork, read-only reference; separate repo taborcaleb)
- Description: Custom ground control station for a friend's 3D-printed RC plane/drone
- UI Style: DJI Agriculture UI/UX — futuristic, HUD overlays, glassmorphic panels
- **Engineering standards: `rc-plane-app\ARCHITECTURE.md`** — UAV architecture directive adopted 2026-07-22 (professional ArduPilot/MAVLink/Mission Planner practices; read before structural changes; report changes against its final-instruction checklist)
- Note: Completely separate from Relevyn

> ### ▶ RESUME HERE (start of next session)
> **Mission context: airframe fully printed, internals imminent. Backend flight-readiness (A1–A5), UI overhaul (B1–B3), AND a full-backend hardening audit are ALL SHIPPED and SITL-validated.** Remaining software work is small (one eyeball check + merging the safety-monitor branch); everything else pre-first-flight is hardware-gated.
>
> **State as of 2026-08-15 (commit `7bb3f60`, pushed, working tree clean):**
> - **Backend:** M1a/M1b/M2/M3/M4 + guardian + preflight gate (M6) + bench kit + soak, **plus the 2026-08-15 hardening pass** (~35 audit findings fixed: guardian stands down from RTL during a landing approach, param sync/restore armed-gated, serial links get 4Hz telemetry so a SiK radio isn't saturated, zone planning FAILS CLOSED with an explicit "Plan anyway" opt-out, waterway-ditch keepout corridors, keepout-overflight warnings, never-raise eventlog — full list in the History entry). **250 unit tests + 10 live SITL scenarios green** (`pytest` / `backend\scenarios.ps1 all`, ~4 min).
> - **UI:** Vite (1s builds), 3D FLY view default (CesiumJS, key-free, attitude-true aircraft, CHASE/ORBIT/FREE cams; 2D forced for planning/drawing), NavRail progressive disclosure, server preflight verdicts + guardian chip/annunciators, SprayPanel zone-failure opt-in + overflight warnings. 6 UI tests green.
> - **Billing:** invoiced through the 2026-08-14 session (cumulative $1,593.22 w/ margin — see table). The 2026-08-15 hardening/billing session is the only unbilled work.
> - **`AgOpsGCS.exe` rebuilt 2026-08-15 (53.7MB) with the hardening pass baked in**, smoke-tested: /api/health ok, app served (title AgOps GCS), /cesium Workers+Assets 200, preflight correctly not-ready with no vehicle, NaN request body → clean 422, fault injection refused without a vehicle.
>
> **Next-work menu:**
> 1. **3D scene eyeball check (user's eyes required):** run `start-all.ps1`, FLY view, confirm the Cesium scene renders and the aircraft's nose leads its trail — if it flies sideways, adjust the heading offset in `MapView3D.jsx`'s `oriProp`.
> 2. **Real Cube bench day** whenever hardware returns (Caleb's telemetry radio + receiver still pending): follow the `bench` scenario sequence — params backup → surface/servo tests → calibrations → first-flight bundle `POST /api/bench/first-flight-params {cells, apply:true}`. Data-USB → COM (115200); **PROP OFF; flight battery only after.**
> 3. Software ideas, none started: M5 mission model/resume + connector-leg rerouting around keepouts (deliberately deferred from the audit — M5-scale), powerline keepouts (OSM power=line), customer-site 3D field preview, alert-threshold unification, Stripe keys (Caleb).
> 4. **Guardian safety-monitor expansion — built on a branch, ready to merge, not yet merged.** A parallel session added EKF-variance / vibration / airspeed-stall-margin monitors to `guardian.py` + widened the flight log, in an isolated worktree (`rc-plane-app\.claude\worktrees\spray-safety-monitors`, branch `worktree-spray-safety-monitors`) while this session owned `main`. Already rebased clean onto `7bb3f60` (zero conflicts — disjoint files/functions), **264 tests green**. `git merge worktree-spray-safety-monitors` whenever wanted. Two new hand-off design docs at repo root from that session: **`SPRAY-FLIGHT-SAFETY.md`** (remaining monitor gaps: bank-angle, live keepout-proximity, wind, pump verification, terrain/AGL, post-flight debrief scorecard, SITL scenario proof — blocked on the shared SITL port) and **`POWERLINE-KEEPOUTS.md`** (OSM power=line buffer keepouts — now unblocked, reuses the waterway-corridor pattern this session's hardening pass just landed in `gis_zones.py`). Full detail in the History entry below.
>
> **Get running — one command:** `.\start-all.ps1` from `rc-plane-app\` (see **Quick start** below). First run needs deps installed once (`pip install -r requirements.txt`, `npm install` ×2) — see the script's header comment or README.md.
>
> **Flagship demo (30 seconds):** SPRAY → Area → box some farmland near Sabetha → **Detect fields in area** (USDA traces the real fields, crop-labeled) → Generate Spray Plan (whole job: spray + orange transits + purple home legs) → Upload → FLY view → ARM & TAKEOFF.
>
> **Commit lineage** since the ag-platform baseline `e248499`: `2d2bc84` (M1a+exe+M1b) → `6fcf8d9` (M2) → `256bbed` (M3) → 2026-08-14 series: `8d3d629` (M4 harness) → `543400b` (guardian) → `eef43d8` (preflight gate) → `517d53f` (bench kit) → `bfd4da7` (soak) → `8e9de0c` (B1 Vite) → `9b50002` (B2 3D view) → `ba10eda` (B3 redesign) → `7bb3f60` (backend hardening, HEAD).
>
> Read **"Dev loop"** + **"Real-hardware bench testing"** below before touching the backend/SITL.

#### Quick start (SITL, no hardware)
One command from `rc-plane-app\`: **`.\start-all.ps1`** — launches SITL + backend + GCS + customer site each in their own window and opens the GCS in your browser. `-NoBrowser` flag skips the auto-open. See `rc-plane-app\README.md` for first-time dependency install and the manual (piece-by-piece) startup steps if you'd rather not use the script.

In the GCS: link chip (top-left) → **Quick Connect → Simulator** (real COM ports appear there too, one click, when the Cube is plugged in).

#### Target Hardware (the friend's aircraft)
- **Flight controller:** Cube (CubePilot/Hex — e.g. Cube Orange/Black/Orange+), runs ArduPlane natively → our whole MAVLink/ArduPlane stack matches it exactly
- **RC transmitter:** RadioMaster (handheld, controls the plane directly via receiver — separate from the GCS link)
- **Still unknown:** telemetry radio for the GCS link (e.g. SiK 915MHz, ELRS, or USB), receiver model
- Note: the RadioMaster TX handles manual stick flying (hardware); our GCS is the laptop-side link for config/monitoring/missions
- Current bench rig: Cube+, RadioMaster TX, ESC + motor (no GPS module, no flight battery on the bench yet)

#### Stack
- **Backend:** Python 3.13 / FastAPI / pymavlink / pyserial. Tests: pytest (unit) + `scenarios.ps1` (live SITL scenarios, marker `sitl`)
- **Frontend:** React 19 + **Vite 8** (migrated off CRA 2026-08-14; `npm start` = vite on :3000, `npm test` = Vitest, build 1s) / Leaflet (2D planning map) / **CesiumJS (3D flight view — key-free, default view)**
- **Communication:** MAVLink protocol over serial/USB telemetry radio
- **Fonts:** Inter everywhere (single-typeface system; tabular numerals via body font-variant-numeric; hierarchy by weight, subtle em-based letter-spacing).
- **Color scheme:** Dark theme, cyan (#00e5ff) accent, glassmorphic panels

#### Architecture
```
rc-plane-app/
├── start-all.ps1                # one-command launcher: SITL + backend + GCS + customer site
├── backend/
│   ├── venv/                    # Python virtual environment
│   ├── requirements.txt         # fastapi, uvicorn, pymavlink, pyserial, pytest, etc.
│   ├── pytest.ini               # markers sitl/slow; plain `pytest` excludes SITL scenarios
│   ├── scenarios.ps1            # named live-SITL scenario runner (field-test|link-loss|...|all)
│   ├── logs/                    # Recorded flight logs + events/ (gitignored, auto-created)
│   ├── tests/                   # 234 unit tests (fakes) + tests/sitl/ (10 live scenarios + harness.py)
│   └── app/
│       ├── main.py              # FastAPI app, CORS, catch-all error handler, router includes
│       ├── vehicle_manager.py   # Singleton MAVLink link: telemetry loop, link watchdog/state machine,
│       │                        #   mission/arm/takeoff/land, param engine, guardian runner, flight logging
│       ├── guardian.py          # GCS-side failsafe monitors + emergency state machine (pure logic)
│       ├── preflight.py         # Server-side go/no-go checklist evaluation (pure logic)
│       ├── eventlog.py          # Structured JSONL ops event log + in-memory ring
│       ├── param_meta.py        # Param write validation (types + curated ranges)
│       ├── config.py            # GCS sysid identity config
│       ├── coverage.py          # Serpentine spray-pass planner (single field)
│       ├── gis_zones.py         # OSM Overpass water/trees/buildings zone lookup (cached)
│       ├── albers.py / cdl.py   # EPSG:5070 projection + USDA CropScape field auto-detection
│       └── routers/
│           ├── connection.py    # Serial port listing, connect/disconnect/status
│           ├── telemetry.py     # GET snapshot + WebSocket stream (10Hz; carries guardian verdicts)
│           ├── mission.py       # Upload/download waypoints, start mission
│           ├── vehicle.py       # Arm/disarm (preflight-gated), takeoff, land, modes, params
│           │                    #   + params/backup (.param) + params/restore (diffs, volatile-skip)
│           ├── safety.py        # Geofence + failsafes + GET/POST /guardian + GET /preflight
│           ├── bench.py         # Bench kit: /surface + /servo exercisers, /calibrate,
│           │                    #   /first-flight-params bundle (DISARMED-only)
│           ├── sim.py           # SITL lifecycle (+speedup/fresh_eeprom) + POST /fault injection
│           ├── fields.py        # Field detect (USDA CDL) / snap (OSM parcels)
│           ├── coverage_routes.py # plan / plan_auto / plan_multi job-planning endpoints
│           ├── orders.py        # Customer order lifecycle (SQLite backend/data/orders.db)
│           └── logs.py          # List + fetch recorded flight logs + GET /events
├── frontend/                    # GCS (operator side), Vite dev on :3000, build/ served by backend/exe
│   ├── index.html               # Vite entry (title "AgOps GCS")
│   ├── vite.config.js           # port 3000 pinned, outDir build/, custom cesiumAssets() plugin
│   ├── src/
│   │   ├── App.jsx              # Main layout — fullscreen map (3D default) + HUD overlays
│   │   ├── App.css              # DJI-style dark theme, glassmorphism, gauges
│   │   └── components/          # all .jsx since the Vite migration
│   │       ├── MapView3D.jsx    # 3D flight view: Cesium, key-free Esri imagery, attitude-true
│   │       │                    #   aircraft, 3D paths/trail/fence cylinder, CHASE/ORBIT/FREE cams
│   │       ├── MapView.jsx      # 2D Leaflet map (planning/drawing): waypoint editing, fields, zones
│   │       ├── NavRail.jsx      # FLY/PLAN/SPRAY primary + MORE group (safety/rc/ctrl/params/logs)
│   │       ├── LaunchControl.jsx # Arm+takeoff flow; renders SERVER preflight verdicts; force/override
│   │       ├── FlightVitals.jsx # In-flight console: vitals pill, guardian chip, RTL/LAND
│   │       ├── AlertCenter.jsx  # Annunciators + voice (incl. guardian warning/RTL banners)
│   │       ├── TopBar/HudLeft/HudRight/HudBottom.jsx   # status strip + instrument clusters
│   │       ├── MissionPanel/SprayPanel/SafetyPanel/ParamsPanel/LogsPanel.jsx
│   │       ├── RCPanel.jsx      # RC bars + gamepad/RadioMaster manual control (own WS)
│   │       ├── ControlsPanel.jsx / VideoFeed.jsx / FlightSummary.jsx
│   │       └── ErrorBoundary.jsx / ConnectionOverlay.jsx
│   └── package.json
└── web/                          # Customer ordering site ("PrairieSpray"), Vite/React, port 3001
    ├── src/                      # own deliberate LIGHT design system (see index.css header) — not the GCS theme
    └── package.json
```

#### API Endpoints
- `GET  /api/health` — health check
- `GET  /api/connection/ports` — list serial ports
- `POST /api/connection/connect` — connect to vehicle (connection_string, baud)
- `POST /api/connection/disconnect` — disconnect
- `GET  /api/connection/status` — connection status
- `GET  /api/telemetry/` — current telemetry snapshot
- `WS   /api/telemetry/ws` — real-time telemetry stream (10Hz)
- `GET  /api/mission/download` — download mission waypoints from vehicle
- `POST /api/mission/upload` — upload waypoints to vehicle
- `POST /api/mission/start` — start mission (sets AUTO mode)
- `POST /api/vehicle/arm` — arm vehicle (body {force, override}); **refused (409, blockers named) while a pre-flight blocker fails** unless override; waits for COMMAND_ACK, reports real result
- `POST /api/vehicle/disarm` — disarm vehicle (body {force})
- `POST /api/vehicle/takeoff` — arm + auto-takeoff flow (body {alt, force, override}); same pre-flight gate; loads takeoff+loiter mission, sets AUTO, arms
- `POST /api/vehicle/land` — auto-land flow; loads approach + NAV_LAND at home, flies it in AUTO, auto-disarms on touchdown
- `POST /api/vehicle/mode` — set flight mode
- `WS   /api/vehicle/rc` — stream {channels:[...]} to fly via RC override (gamepad/RadioMaster); releases override on disconnect
- `GET  /api/vehicle/modes` — list available modes
- `POST /api/vehicle/params` — set a flight controller parameter
- `GET/POST /api/safety/geofence` — read/apply circular geofence (FENCE_* params)
- `GET/POST /api/safety/failsafe` — read/apply battery/GCS/RC failsafes
- `GET /api/safety/preflight` — server-side go/no-go checklist (same evaluation that gates /arm)
- `GET/POST /api/safety/guardian` — guardian config (partial updates) + live monitor/state verdicts
- `GET /api/vehicle/params/backup` — full .param backup (Mission Planner format; needs full cache sync)
- `POST /api/vehicle/params/restore` — write a backup back (diffs only, verified, volatile params skipped)
- `POST /api/sim/start` — spawn bundled SITL (body {speedup, fresh_eeprom}; fresh runs in sitl/_scenario/)
- `POST /api/sim/fault` — inject/clear faults: gps | battery (voltage) | gcs_link (heartbeat suppression)
- `POST /api/bench/surface` — deflect a control surface via MANUAL+RC-override hold (DISARMED-only; throttle needs allow_throttle)
- `POST /api/bench/servo` (+`/release`) — DO_SET_SERVO on unassigned aux channels (spray pump path)
- `POST /api/bench/calibrate` — gyro | baro | level | accel_simple | mag_start/accept/cancel
- `POST /api/bench/first-flight-params` — ops-safety param bundle by cell count (preview / atomic apply)
- `GET /api/logs` — list recorded flights; `GET /api/logs/{name}` — fetch samples for playback; `GET /api/logs/events` — ops event log
- `POST /api/fields/detect` — USDA CDL field auto-detection inside a boxed area
- `POST /api/fields/snap` — OSM ag-parcel boundary snap
- `POST /api/coverage/plan` / `plan_auto` / `plan_multi` — single-field / zone-aware / whole-job spray planning
- `/api/orders/*` — customer order lifecycle (create, pay, status)

Full interactive docs at `http://localhost:8000/docs` once the backend is running.

#### Decisions Made
- **Dropped dronekit** — incompatible with Python 3.13 (collections.MutableMapping removed). Using pymavlink directly instead.
- **Dropped Mission Planner as base** — 337MB legacy C#/WinForms app, too heavy to iterate on. Kept fork as reference only.
- **Chose custom web app** — faster iteration, modern stack, only build what's needed
- **DJI Ag UI style** — fullscreen map, HUD overlays, no traditional sidebar layout
- **Must request MAV_DATA_STREAM on connect** — ArduPilot only sends heartbeats otherwise; without it attitude/GPS/battery stay at 0. (Fixed in vehicle_manager.connect)
- **Display relative altitude** (height above home), not AMSL — GLOBAL_POSITION_INT.relative_alt, not VFR_HUD.alt
- **Link lock (_link_lock)** — the telemetry thread and mission upload/download both read the same MAVLink connection; a lock serializes them so they don't steal each other's messages. Mission ops hold it for the whole transaction. (Without this, mission upload hangs.)
- **Mission convention** — backend auto-inserts home as seq 0; user waypoints start at seq 1; download strips seq 0. Supported commands: TAKEOFF/WAYPOINT/LOITER/LAND/RTL.
- **All vehicle endpoints run in a threadpool, never on the event loop** — pymavlink is blocking; one slow call used to freeze the whole API including emergency RTL/disarm. Fixed in the refinement audit (see History).

#### Dev loop (important)
- The SITL build EXITS when the GCS/backend disconnects. Restarting the backend (no `--reload` in use) drops the link → SITL exits. So to reload backend code: stop backend + SITL, relaunch SITL (`sitl/run_sitl.bat` or ArduPlane.exe with `-O 39.9042,-95.7997,408,0`), relaunch backend, reconnect to `tcp:127.0.0.1:5760`, wait ~28s for GPS. `start-all.ps1` automates the launch order but you still need to relaunch it after a backend code change.
- Force-arm (21196) + AUTO+mission is the validated way to fly SITL; pre-arm checks sometimes pass, sometimes need force.

#### Real-hardware bench testing notes
- **Cube connects over direct USB → COM3 @ 115200.** In the connect dialog use `COM3` (or whatever COM the Cube enumerates as; VID_2DAE = Hex/ProfiCNC = the Cube).
- **Cable gotcha (cost us a while):** the Cube's micro-USB cable was CHARGE-ONLY (LED on, but no COM port ever appeared). A charge-only cable powers the Cube but carries no data. Verify a cable with the phone "file transfer?" test. A known data cable finally enumerated COM3.
- **RadioMaster manual control: VALIDATED with real hardware.** RadioMaster (USB-C, EdgeTX "USB Joystick (HID)" mode) → browser Gamepad API → backend RC override → sim. Mapping was correct out of the box (right sticks = right controls). This is the RCPanel "Manual Control" feature.
- On the bench the Cube correctly reads **GPS fix=0 / 0 sats** (no GPS) and **0V** (no battery) — expected, confirms real data after the telemetry-reset fix.
- **Cube USB link was flaky / dropped** during testing — partly a person physically unplugging it, but micro-USB on the Cube is finicky; reseat firmly and don't tug the cable. Backend auto-reconnect (shipped 2026-07-12) now handles a brief drop automatically.

#### Known gotchas
- Browser screenshot automation (claude-in-chrome) errors on localhost frames ("Frame showing error page") — verify UI by having the user look, plus `react-scripts build` compile check + tile HTTP checks
- OSM parcel coverage near Sabetha is sparse → `/api/fields/snap` and OSM-fallback detect often find 0 there (normal, message guides to Draw/Snap)
- USDA CDL despeckle can bridge a 1px pinch between two adjacent fields — delete+redraw if separation matters
- CropScape's `GetCDLData` endpoint was retired server-side; `cdl.py` uses `GetCDLFile` — check the WSDL first if field detection breaks again

#### Still TODO
- **3D scene eyeball check** (only open software item from 2026-08-14): run `start-all.ps1`, confirm the Cesium view renders and the aircraft's nose leads its trail; if sideways, adjust the heading offset in MapView3D's oriProp.
- **Hardware-gated:** Cube bench day (script = the `bench` scenario sequence: backup → surface/servo tests → calibrations → `POST /api/bench/first-flight-params {cells, apply:true}`); telemetry radio + receiver (Caleb's task); Stripe live keys (Caleb); WebRTC/HLS video (needs HD video hardware); terrain intelligence phase 2 (needs camera + companion computer).
- **Software next-ideas (not started):** M5 mission model & resume; powerline keepouts (OSM power=line buffers); customer-site 3D field/flight preview; alert-threshold unification (AlertCenter vs guardian config). Billing table updated 2026-08-15 (covers through the 2026-08-14 session; the 2026-08-15 session itself is the only unbilled one).

#### Design ideas (proposed, NOT implemented; user hasn't picked yet)
- Tier 1 (recommended as one "flight safety & feedback" release — mostly now shipped, see History "List-finish session"): pre-flight checklist card gating ARM (with override); aviation-style alerts/annunciators + optional voice callouts; post-flight summary card on disarm; "RTL margin" can-I-get-home indicator
- Tier 2: FPV mode (video fullscreen + HUD overlay, map becomes PiP — the showpiece, wants camera hardware); instrument cards w/ sparklines replacing bottom strip; flight-phase adaptive layout; mission altitude-profile ribbon
- Tier 3: day/night/field themes; map long-press radial menu; first-run tour; tablet layout

#### Billing — Claude usage cost basis (for invoicing Caleb)
Running log of Claude Code token-usage cost, computed at Anthropic API list-price equivalent (not actual subscription cost — Claude Code runs on a Max/Pro plan, this is a billing proxy). Method: parse local session transcripts (`~/.claude/projects/.../*.jsonl`) filtered to `cwd` under `rc-plane`/`rc-plane-app`, sum `usage` tokens per model (input/output/cache-write-5m/cache-write-1h/cache-read), price at current API rates, include workflow subagent transcripts (`subagents/workflows/*/agent-*.jsonl`).

| Date logged | Period covered | Cost basis (API list-price) | +20% margin | Notes |
|---|---|---|---|---|
| 2026-07-21 | 2026-07-10 → 2026-07-13 (main session + 44 workflow subagents: ag-platform round 1/2, refinement-audit) | $1,182.69 | **$1,419.23** | Session `29330544-8377-4fcf-a93f-a4c0c39cc962`. Breakdown: main session opus-4-6 $5.12 + opus-4-8 $184.99 + fable-5 $705.36; subagents fable-5 $279.54 + opus-4-8 $7.68. |
| 2026-08-14 | 2026-07-21 → 2026-08-14 (4 sessions + subagents: docs/start-all pass, directive+M1a, M1b/M2/M3, and the big 08-14 both-tracks session) | $144.99 | **$173.99** | Sessions `58e2dfa5` (docs pass, sonnet-5 $4.62), `d2552655` (directive+M1a, opus-4-8 $14.02 + sonnet-5 $0.77), `81187eb4` (M1b/M2/M3, opus-4-8 $30.79 + sonnet-5 $0.23), `5c00e666` (M4→soak + UI B1–B3, fable-5 $92.50 + opus-5 $2.05). Excludes the still-open 2026-08-15 session (3D eyeball check + this billing update) — roll it into the next row. |

**Cumulative:** cost basis **$1,327.68**, with margin **$1,593.22**.

**To extend this table in a future session:** re-run the same transcript-parsing method for any *new* session(s) since the last logged period (check `~/.claude/projects/C--Users-tabor-rc-plane-app/` for the current session ID, and `~/.claude/projects/C--Users-tabor/<session-id>.jsonl` + its `subagents/` dir for token usage), add a new row, and keep a running cumulative total if useful.

---

## History (session-by-session build log)
Newest first isn't required — kept chronological. This section is an append-only record;
add a new dated entry here rather than editing old ones. Everything above the `---` is the
"living" reference — keep that current and reorganize freely.

#### Guardian safety-monitor expansion (2026-08-15) — SHIPPED on branch, not yet merged
- **Two Claude Code sessions ran in parallel on the same repo** — this one and the session that
  shipped the "Backend hardening" entry below. Kept safe via a **git worktree**
  (`rc-plane-app\.claude\worktrees\spray-safety-monitors`, branch `worktree-spray-safety-monitors`)
  rather than editing the shared checkout directly: separate working directory, own venv, own
  branch off the last commit at the time (`ba10eda`), zero risk of stepping on the other
  session's uncommitted work or its running SITL/backend/frontend on the default ports.
- **Three new `guardian.py` monitors**, all following the existing warn-by-default /
  RTL-if-explicitly-configured pattern: **EKF variance** (`pos_horiz_variance` /
  `velocity_variance` from `EKF_STATUS_REPORT` — previously only the binary `ekf_healthy` flag
  was read, and only once, pre-arm, by `preflight.py`; nothing watched EKF continuously in
  flight before this). **Vibration** (`VIBRATION` message, not subscribed to at all before —
  RMS per-axis + accelerometer clip-count, baselined at the arm edge since clip counters are
  cumulative since boot, not since arm). **Airspeed/stall margin** (airspeed already flowed from
  `VFR_HUD` but nothing watched it — gated on being airborne via altitude, not just armed, so
  taxiing / the takeoff roll never false-triggers a stall warning).
- **Flight log widened** (`_maybe_log`, 4Hz JSONL-per-flight): now also carries GPS fix/sats, EKF
  flags+variance, vibration+clip counts, sensor errors, and mission waypoint context — was
  position/attitude/speed/battery/mode only, so a near-miss that never escalated to a guardian
  action (a variance spike, a vibration burst) was previously invisible after the fact.
- **264 backend tests green** (+14 new guardian tests on top of the other session's own +16).
  Rebased cleanly onto that session's `7bb3f60` hardening-pass commit — confirmed zero conflicts
  both by a clean `git rebase` and by diffing the actual changed regions beforehand: both
  sessions touched `vehicle_manager.py`, but in disjoint functions (this session: `TelemetryData`
  fields, `_handle_msg`'s message parsing, `_maybe_log`; the other: `_request_data_streams`,
  `_guardian_tick`'s landing-RTL suppression, `snapshot()`'s NaN scrubbing). `guardian.py` itself
  wasn't touched by the other session at all.
- **Two new hand-off design docs promoted to repo root** (were session scratch notes, moved so
  they survive past the session): **`SPRAY-FLIGHT-SAFETY.md`** — full gap analysis of what's
  done vs. still needed for "the drone doesn't crash while spraying": bank-angle monitor, live
  keepout-proximity monitor (cross-checking actual flown position against keepout rings in real
  time, not just the planned path), wind monitor, pump/spray-system verification (hardware-gated
  — needs Caleb to confirm what sensing the pump path has), terrain/AGL clearance (deferred,
  needs the camera/companion-computer phase), a post-flight "how close did we come" debrief
  scorecard, and SITL scenario proof for all of the above (currently blocked — the scenario
  harness hardcodes the same default SITL port `5760` the live dev instance was holding).
  **`POWERLINE-KEEPOUTS.md`** — OSM `power=line`/`power=minor_line` buffer keepouts, designed to
  reuse the exact waterway-corridor pattern (`_corridor_ring`, the linear-tag branch in
  `_parse_overpass`) the hardening pass below just landed in `gis_zones.py`; not started, now
  unblocked since that refactor landed.
- **Open question surfaced, not resolved:** `CoverageRequest.alt` defaults to 100m AGL, well
  above real crop-dusting altitude — worth confirming the actual target spray altitude with
  Caleb, since it reorders which remaining monitor gaps (bank-angle, terrain/AGL) are urgent
  versus theoretical for a while yet.
- **Not yet merged to `main`** — branch is rebased onto `7bb3f60`, tested, and ready;
  `git merge worktree-spray-safety-monitors` whenever wanted.

#### Backend hardening audit + fixes (2026-08-15) — full-backend defect sweep SHIPPED
- **Full audit of every backend module** (~5,000 lines; core read line-by-line, planning/GIS + orders/logging swept by parallel reviewers): ~35 findings, all actionable ones fixed same session. Themes: silent fail-open paths, concurrency around the link lock, SITL-vs-real-hardware differences.
- **Flight-critical fixes:** eventlog field-sanitization loop moved inside the never-raise guard (could kill the telemetry thread; disk I/O also moved off the ring lock, UTC roll, core-field clobber guard, NaN-safe); **guardian now stands down from RTL during an active landing approach** (battery-low would have aborted a final approach into a climb on a dying pack — warn-only until landing done/aborted; stale landing latch also self-clears when the operator leaves AUTO); **full param sync/restore refused while ARMED** (held `_link_lock` up to ~25s, queueing RTL/disarm/guardian behind it); **telemetry stream rate is link-aware** (10Hz TCP/UDP, 4Hz serial — ALL@10Hz would saturate a 57600 SiK radio on hardware day; `TELEM_RATE_HZ` env overrides); `land()` refuses without a real HOME_POSITION (used to silently "land" at current position) and hints RTL; takeoff reverts mode if arming fails; snapshot() NaN-scrubbed (NaN telemetry made every WS frame invalid JSON) + `_hb_times` deque race fixed; mission items bounds-checked (int32 packing overflow aborted transfers without cancel; send failures now still cancel the transaction); sim fault injection refuses non-SITL vehicles (gcs_link fault would RTL a real plane).
- **Planner/GIS fail-closed chain:** zone-service failure now REFUSES to plan (502) unless `allow_missing_zones=true` — UI shows a red "Plan anyway — NO no-spray zones" opt-in; Overpass HTTP-200 "remark" runtime errors detected (were parsed as "no zones here" and cached 10 min); `plan_auto` zone radius formula fixed (bbox half-diagonal from vertex centroid could leave field edges outside the queried disc); **linear waterways (streams/ditches/drains) now become water keepout corridors** (out-and-back rings the Minkowski buffer turns into corridors); multipolygon outer ways endpoint-stitched (chord-closing left concave-lake interiors sprayable); **connector legs crossing keepouts are counted** (`keepout_overflights` in stats/totals, amber UI warning — legs are still not rerouted; that's M5-scale); clip-work CPU budget shared across a whole plan_multi job (was per-field ×25); CDL despeckle no longer fills protected classes (a 30m pond pixel is a pond, not speckle) + MIN_HOLE_PX 2→1 + raster-mode sanity + 90s total fetch budget + year in cache key; OSM detect fallback flagged (`cdl_unavailable`, explicit empty `holes`); parcel decimation replaced with deviation-bounded DP (stride bulged outward across concavities); snap-to-field by nearest boundary not centroid; truncation flags on capped lists; global 422 sanitizer in main.py (NaN in request bodies 500'd across routers).
- **Robustness:** logs router off the event loop + malformed-line tolerant; orders coordinate ranges/vertex caps/length caps/zero-area rejection; bench surface test restores prior mode; caches keyed at 4dp (~11m).
- **Deliberately deferred:** connector-leg rerouting around keepouts (M5-scale, now surfaced honestly instead), reconnect-resets-guardian-debounce (low impact, risky change), event-log retention, CDL geotransform-fallback pixel shift.
- **250 unit tests green** (+16, incl. new `test_flight_hardening.py`: landing standdown, armed gates, NaN snapshot, eventlog never-raises); 6 UI tests green; frontend builds.

#### UI track B3 (2026-08-14) — progressive disclosure + one source of truth SHIPPED
- **NavRail progressive disclosure:** the default screen now carries exactly three choices — FLY / PLAN / SPRAY — with SAFETY/RC/CTRL/PARAMS/LOGS behind a MORE (⋯) group (stays open while one of its views is active).
- **LaunchControl renders SERVER preflight verdicts** (polls `/api/safety/preflight` at 2.5s while disarmed): the checklist the operator sees is byte-for-byte the gate the backend enforces (M6 "UI renders verdicts" complete). Minimal local fallback (link+GPS) so the panel is never blank.
- **Guardian surfaced in the UI:** FlightVitals shows a guardian state chip (amber warnings / pulsing red for RTL_REQUESTED/ACTIVE with the recorded reason); AlertCenter adds guardian-warning and guardian-RTL annunciators with voice callouts. The backend's failsafe brain is now visible+audible where the operator looks.
- **Deleted the 3 dead components** (ConnectionPanel, FlightControls, TelemetryDashboard, ~266 lines).
- **Customer site (PrairieSpray) deliberately NOT restyled:** web/src/index.css documents its own intentional design system ("Deliberately NOT the dark operator GCS theme — customers get a light, warm, trustworthy look"). Operator HUD ≠ customer storefront; both already share Inter + the agriculture-green brand. Churn avoided; noted for the user. Future site upgrade candidate: customer-facing 3D field/flight preview (would pull Cesium into web/). Acreage-math dedup (3 copies) also left — cross-app extraction, low value.
- **`AgOpsGCS.exe` rebuilt (56MB)** with the full new frontend; smoke-tested: serves the Vite app (title AgOps GCS) AND /cesium assets. Both frontends build; 6 UI tests green.

#### UI track B1+B2 (2026-08-14) — Vite migration + 3D flight view SHIPPED
- **B1 Vite migration:** react-scripts 5 → Vite 8 + Vitest (JSX files → .jsx; root index.html titled "AgOps GCS"; dev port pinned 3000 for api.js origin sniffing; outDir stays `build/` for main.py + PyInstaller). Dead CRA baggage deleted (reportWebVitals, logo.svg, public/index.html, web-vitals, never-imported recharts). Build 60s→1s. 6 UI tests green under Vitest.
- **B2 3D flight view:** `src/components/MapView3D.jsx` — **CesiumJS 1.144, entirely key-free** (same Esri World_Imagery tile URL as 2D; ellipsoid terrain — Kansas is flat, world is ground=0 + relative altitude). Aircraft = primitive box composition (fuselage/wing/tail/fin) that **banks & pitches with live attitude** via HPR quaternion (north-west-up frame: +X nose, heading direct from telemetry); altitude drop-line + ground shadow dot; live 3D trail; mission waypoints + path at real altitude with drop-lines; spray legs at planned altitude (sprayLegs/playbackPath now carry [lat,lon,alt] — Leaflet ignores the 3rd element); field polygons w/ holes; zones; **geofence as translucent 3D cylinder**; cameras: CHASE (behind aircraft along heading, preRender lookAt), ORBIT (trackedEntity), FREE (+2D button).
- **App split:** 3D is the DEFAULT; `plan` view and any active spray drawing force the flat map (editing is top-down); "◈ 3D" button returns. MapView3D is React.lazy code-split (Cesium ~1.2MB gz in its own chunk). All scene updates imperative via CallbackProperties reading a ref — App's 10Hz re-render costs nothing.
- **Cesium hosting:** custom Vite plugin `cesiumAssets()` in vite.config.js (vite-plugin-static-copy nested wrongly on Vite 8) — dev middleware streams /cesium/* from node_modules; build copies Workers/ThirdParty/Assets/Widgets into build/cesium (6.8MB). `CESIUM_BASE_URL` defined at build. Verified: assets 200 over dev server, page loads, tests+build green.
- **NOT yet verified visually** (localhost browser-automation gotcha): the rendered scene + aircraft model orientation. **First `start-all.ps1` run: eyeball that the model's nose leads the trail; if it flies sideways, adjust the heading offset in MapView3D's oriProp.** Exe NOT yet rebuilt with the new frontend — rebuild after B3.

#### Soak test + exe rebuild (2026-08-14) — backend flight-readiness track COMPLETE
- **A5 `soak` scenario** (scenarios.ps1): a real spray job (planned by `/api/coverage/plan`, ~20 waypoints) flown fully autonomously at speedup 5 WHILE two telemetry WebSocket readers + API pollers (telemetry/status at 5Hz; events/cached-params/guardian at 2Hz) load the backend the way the real UI does. Asserts over the whole flight: WS never stalls >5s (thousands of frames), link never LOST, mission completes + returns home, zero `core/unhandled_exception` + zero `guardian/tick_error` events, process RSS growth <150MB (Windows psapi via ctypes). **Passed first run: 93s, clean.**
- **Full verification state: 234 unit tests + 10 live SITL scenarios green** (`scenarios.ps1 all` = 4min07). 
- **`AgOpsGCS.exe` rebuilt 2026-08-14** (51MB) with the entire track baked in: M4 harness endpoints, guardian, preflight gate, bench kit, LaunchControl override passthrough. Smoke-tested: /api/health ok, /api/safety/preflight correctly not-ready with no vehicle, guardian config live, bench bundle preview live.
- **The backend track (A1–A5) is done.** What remains before a real first flight is hardware-gated only: Cube bench test (use the `bench` scenario sequence as the script: backup → surface tests → calibrations → first-flight bundle), telemetry radio (Caleb), and a real-hardware run of the preflight gate. Software next = UI track B1–B3 (Vite migration → 3D FLY view → Apple-simple redesign).

#### Bench & first-flight kit (2026-08-14) — SHIPPED, internals-day rehearsed live
- **A4: the setup tooling for the day the electronics go in.** New `/api/bench` router (all DISARMED-only) + param backup/restore on the vehicle router.
- **Surface test** `POST /api/bench/surface {surface, pwm, hold_s, allow_throttle}` — MANUAL mode + RC override streamed ~20Hz for the hold, then always released; samples SERVO_OUTPUT_RAW into the response so "did it move" has a telemetry answer. Uses RCMAP_* to find channels. **Hard-won firmware truths:** DO_SET_SERVO is REFUSED on function-assigned channels (result=4) and runtime-parking SERVOn_FUNCTION=0 does NOT take effect — the RC-passthrough path is the correct surface exerciser. Output PWM ≠ input PWM (scales into SERVO_MIN/MAX; 1900→1820 observed).
- **Aux servo test** `POST /api/bench/servo` — DO_SET_SERVO, unassigned channels only (SERVO5+ on defaults; this is the spray-pump path later). `/servo/release` returns it.
- **Calibration** `POST /api/bench/calibrate {kind: gyro|baro|level|accel_simple|mag_start|mag_accept|mag_cancel}` via new generic `vehicle_manager.run_command` (COMMAND_LONG + ack, IN_PROGRESS counts as accepted).
- **Param backup/restore**: `GET /api/vehicle/params/backup` (.param text, MP-compatible, 409 if cache not fully synced) / `POST /api/vehicle/params/restore` (only diffs written, each M1b-verified, full outcome report; **skips volatile params** — `*_GND_PRESS` live re-estimates and can never verify, `STAT_*`).
- **First-flight bundle** `POST /api/bench/first-flight-params {cells, fence_radius_m, fence_alt_max_m, rtl_alt_m, apply}` — preview by default; apply = atomic (rollback) + sets guardian batt thresholds above the vehicle's own (3.65/3.55 per cell vs 3.5/3.3). Ops-safety params ONLY, deliberately no airframe tuning. **Firmware-aware names:** ArduPlane 4.8 has ARMING_SKIPCHK(0)/ARMING_REQUIRE(1) + RTL_ALTITUDE (METERS) — no ARMING_CHECK/ALT_HOLD_RTL; bundle picks by cache like the MAV_GCS_SYSID story.
- **234 unit tests** (+16); scenario `bench` (scenarios.ps1) = the internals-day dress rehearsal live: backup → aileron deflection via real pilot path → aux servo 5 → both prop-off guards (assigned-channel 409, throttle-consent 409) → baro cal → bundle apply (preflight fence advisory flips green, BATT_LOW_VOLT read-back 10.5) → restore round-trip (WP_RADIUS back to 90; 1419 identical skipped). **All 9 scenarios green serially (153s).**

#### Server-side pre-flight gate (2026-08-14) — SHIPPED, live-proven (M6 core)
- **A3: the go/no-go authority moved from the UI checklist to the backend.** `app/preflight.py` (pure evaluation, guardian.py discipline): blockers link-READY / GPS-3D / EKF-healthy / home-known; advisories battery / RC-seen / fence-enabled / sensors-healthy (inform, never block). `GET /api/safety/preflight` returns the full checklist; **/arm and /takeoff run the same evaluation and 409 with the blockers NAMED while any fails** — `override:true` (distinct from `force`, which is ArduPilot's 21196 pre-arm bypass) skips the gate, and both refusal and override land in the event log.
- LaunchControl: OVERRIDE toggle now sent to the backend; error toast formats the structured 409 (names the failing blockers). Frontend builds clean. Full server-verdict rendering = B-track.
- **218 unit tests** (+9); scenario `preflight` (in scenarios.ps1): arm right after boot → 409 (GPS not converged), same request after readiness → armed. **All 8 scenarios green serially (142s).**

#### Guardian layer (2026-08-14) — SHIPPED, proven live by its own scenario
- **A2 of the flight-readiness track: GCS-side failsafe monitors + emergency state machine** (ARCHITECTURE.md "Failsafe architecture" — was entirely absent). ArduPilot onboard failsafes remain primary; the guardian is the second, earlier, smarter layer.
- `app/guardian.py` = PURE logic (no locks/side effects, every rule unit-testable): monitors gated on ARMED (a bench vehicle with no GPS/battery is normal, not an emergency). Link (graded-level warn), GPS (fix loss/thin sats — **warn-only by default**, deliberate: RTL without GPS can't navigate; ArduPilot dead-reckoning is the right reaction), battery (warn volt + sustained-low RTL, debounced 10s like BATT_LOW_TIMER), **RTL energy margin** (needs `pack_capacity_mah` config; consumed+current+distance-home → time margin with reserve; the thing the autopilot doesn't watch).
- Emergency state machine NORMAL→WARNING→RTL_REQUESTED→RTL_ACTIVE→LANDING→DISARMED. RTL_ACTIVE = vehicle truth whoever commanded it; LANDING via `_landing_requested` flag set by land(); DISARMED sticky after flight, reset on arm rising edge (which also resets debounce memory).
- Runner: 1Hz `_guardian_tick` thread in vehicle_manager (started on connect, dies with link, tick exceptions can never kill the link). Guardian RTL: **trigger event logged BEFORE set_mode** (directive), ack-verified, 3-attempt cap then loud give-up. **Operator override → standdown**: if mode leaves RTL while the condition persists, log `overridden_by_operator` and stand down for that source until it clears — never fights the pilot.
- API: `GET/POST /api/safety/guardian` (partial updates, batt threshold-order validated). Verdicts in `snapshot()['guardian']` → flow to the UI over the existing telemetry WS (UI rendering = B-track item).
- **209 unit tests** (+23 `test_guardian.py`); scenario `tests/sitl/test_scenario_guardian.py` (also in scenarios.ps1): ArduPilot battery FS disabled → sag 10.3V → guardian debounce → `rtl_triggered(reason: battery…)` → RTL → RTL_ACTIVE. **All 7 scenarios green serially (137s).** Scenario conftest resets guardian config between scenarios (shared singleton).

#### M4 SITL scenario harness (2026-08-14) — SHIPPED, all 6 scenarios validated live
- **Context:** airframe fully printed, internals imminent — priority flipped to "unshakeable backend for a fully autonomous first flight." M4 built first because it's the proof machinery everything later (guardian failsafes, preflight gate) will be validated with. UI track (3D FLY view + Apple-simple redesign, Vite migration prerequisite) deliberately queued BEHIND flight-readiness.
- **pytest infra (first in repo):** `backend/pytest.ini` (markers `sitl`/`slow`, default `-m "not sitl"` so plain `pytest` stays fast; explicit `-m sitl` overrides), pytest added to requirements. Existing 174 unittest tests collect unchanged.
- **Sim router:** `POST /api/sim/start {speedup (0.5-20), fresh_eeprom}` — fresh_eeprom runs SITL in `sitl/_scenario/` (gitignored) with eeprom deleted → firmware-default boots, demo eeprom NEVER touched (verified live by timestamps). `POST /api/sim/fault {fault: gps|battery|gcs_link, enable, value}`: gps = firmware-aware `SIM_GPS1_ENABLE` (4.5+) / `SIM_GPS_DISABLE` (legacy) picked from the param cache; battery = `SIM_BATT_VOLTAGE` sag with previous-voltage restore on clear; gcs_link = suppresses OUR 1Hz heartbeat (`vehicle_manager.set_gcs_heartbeat_suppressed`, exposed as `gcs_hb_suppressed` in snapshot, force-cleared on every connect). All param faults ride the verified M1b path; SIM_* params added to `param_meta.PARAM_RANGES`. No-body `POST /start` (UI Simulator button) unchanged — unit-tested.
- **Six scenarios** (`tests/sitl/`, real FastAPI app via TestClient ↔ real bundled SITL, all through public API): field-test (fresh eeprom → autonomous takeoff→2 waypoints→RTL→auto-land→self-disarm, zero sticks), link-loss (GCS silenced → vehicle self-RTLs → obeys after recovery), gps-failure (fix collapse surfaced, link unshaken, recovers), battery-fault (sag → RTL), rtl-recovery (commanded abort converges home, mission resumes at aborted seq), link-watchdog (SITL killed under backend → LOST → auto-reconnect self-heals to READY; first-ever real-process proof of that path). Runner: `backend\scenarios.ps1 <name>|all`.
- **TIMING RULE (learned/encoded in harness docstring):** our GCS heartbeat is 1Hz WALL time but FS_GCS times in SIM seconds → any scenario with `FS_GCS_ENABL=1` must run at speedup 1; fast scenarios keep it 0.
- **Validated live 2026-08-14:** each scenario individually + `scenarios.ps1 all` serially — **6/6 passed, 124s**. Unit suite **186 passed** (+12 `test_m4_sim.py`).
- **Bug found by the harness, fixed:** SITL dying mid-param-sync killed the background sync thread, leaving `param_sync.syncing=true` forever (UI = eternal spinner). `get_all_params` now wraps the transaction; failure → clean state + `param/sync_failed` WARN event.
- **Next:** A2 guardian layer (GPS/battery/link monitors + NORMAL→WARNING→RTL emergency state machine, validated via this harness), then A3 server-side preflight gate (M6), A4 bench kit (servo/motor test, calibration, param backup — needed when internals arrive), A5 soak test. Then UI track B1-B3.

#### Ultracode round 1 (2026-07-11) — ag-platform foundation SHIPPED (commit f157e18)
- **Coverage engine:** `backend/app/coverage.py`, POST `/api/coverage/plan` {polygon, swath, alt} → serpentine spray waypoints + stats. Reviewer caught half-swath-gap HIGH bug (fixed, verified over 591 geometries). 42 backend tests.
- **GIS zones:** `backend/app/gis_zones.py`, GET `/api/zones?lat&lon&radius` → water/trees/buildings from OSM Overpass (TTL+size-capped cache). Sabetha live: 26 water, 184 buildings.
- **Orders + customer site:** `backend/app/routers/orders.py` (SQLite backend/data/orders.db, server-side $12/acre min $150, status chain, dev-pay w/ Stripe TODO slot) + `web/` "PrairieSpray" Vite/React site on **port 3001** (`cd web && npm run dev`; /api proxied to :8000). Flow: land→contact→draw field on satellite→schedule→pay→confirm.
- **GCS now SENDS 1Hz heartbeats** (vehicle_manager) — without them ArduPilot's FS_GCS failsafe RTLs any mission. Found live.
- **Fence lesson:** old 500m test fence in SITL eeprom vetoed the spray run ("Circle fence breached" → RTL). Diagnose failsafes via STATUSTEXT on second link (tcp:5762). Fence resized 2500m, kept enabled. WP_RADIUS=30 for 40m pass spacing (turnaround entry WPs auto-skip by design — passes still fly true).
- **DEMO VALIDATED:** order ($787.45 / 65.6ac) → plan (10 passes) → 22-item mission → SITL flew the pattern. Full customer→drone pipeline works.

#### Ultracode round 2 (2026-07-11) — zone-aware spraying SHIPPED (commit a07779c)
- **Keepout planner:** plan_coverage(keepouts, keepout_buffer_m) clips spray segments around zones (exact buffered subtraction); POST `/api/coverage/plan_auto` = one-call zones+plan (per-kind buffers water 15/trees 10/buildings 10, degrades w/ zones_unavailable). Payload caps vs CPU abuse. 60 backend tests.
- **GCS SPRAY view:** draw field or load order by id → zones overlay + clipped path + stats → Upload Mission. Amber warn when path doesn't avoid zones.
- **Site r2:** customer sees their drone's planned path at review; order tracking timeline page.
- **WATER DEMO validated:** lakeside 82ac field (lake @ 39.91648,-95.79968) — blind plan min-water-dist 0.0m; auto plan 20 segments, 6 zones clipped, min dist exactly 15.0m; flown as 42-item mission in SITL. Demo field staged in scratchpad water_demo.json (regenerate anytime).
- Note: reviewers in shared-tree parallel lanes cross-flag sibling files — expected; verify via lane manifests.

#### List-finish session (2026-07-12) — SHIPPED (commits 02a8178..a79bb55)
- ✅ Backend auto-reconnect (unexpected link loss retries last connection 20×5s; operator disconnects don't; status.reconnecting; validated live kill/restore)
- ✅ Boundary snap (task #7 done): /api/fields/snap (OSM ag parcels) + SPRAY 'Snap' mode; graceful manual-draw fallback where OSM is sparse (Sabetha mostly is)
- ✅ Safety pack (task #10 done): pre-flight checklist gating ARM (blockers link/GPS/home + OVERRIDE; advisories batt/RC/fence), AlertCenter annunciator + voice callouts (toggle persisted), RTL margin in FlightVitals (home now in telemetry), post-flight debrief card → replay
- ✅ Mission niceties: waypoint reorder, LOITER radius (param3), save/load mission JSON
- ✅ PARAMS view: full param table download (1440 params <1s SITL), filter, inline write-back

#### Multi-field jobs (2026-07-12) — SHIPPED (commit df0ae1a)
- SPRAY view is now a JOB builder: field list via Draw (multiple) / **Area select → auto-detect mapped fields inside** (POST /api/fields/detect) / Snap / order load. Numbered badges on map.
- **Whole-job planning**: POST /api/coverage/plan_multi — per-field zone-aware plans + greedy nearest-endpoint field ordering (with per-field serpentine REVERSAL when it shortens transit) + explicit transit legs + combined_waypoints + totals. One zone fetch per job; graceful degrade.
- **Full-flight visualization**: spray solid cyan / in-field hops faint / inter-field transit dashed orange / home legs dashed purple, + legend and job stats card.
- Validated live: 2-field 114.8ac job → optimizer reordered fields, 3 transit legs, 7 keepouts, min water dist exactly 15.0m over 60 wps, flown as 62-item mission. 68 backend tests.
- Auto-detect data reality: OSM parcel coverage near Sabetha is sparse → detect often finds 0 there (normal, message guides to Draw/Snap); validated logic via mocked parcels + Bavaria live.

#### USDA imagery field auto-detection (2026-07-12) — SHIPPED (commit f97b9f0)
- "Box an area → software draws around the fields" is REAL: /api/fields/detect now uses the **USDA Cropland Data Layer** (30m satellite land-cover, all US) — cropland pixels segmented into fields, boundaries traced (Moore + Douglas-Peucker), crop-labeled, acreage from pixel count. Water/trees/developed excluded by classification. OSM = fallback (non-US/outage).
- Modules: app/albers.py (EPSG:5070 pure-python), app/cdl.py (CropScape **GetCDLFile** — GetCDLData was RETIRED server-side; check WSDL if it breaks again). Pillow dep added. 6km selection cap, year fallback 2025→2023, cache.
- LIVE validated: 3×3km box NW of Sabetha → **12 real fields, 1,013 ac** (350ac corn ×2, 218ac soybeans, hay/alfalfa) in ~10s; auto-detected fields chained into plan_multi with zero hand-drawing.
- Known gap: **powerlines** not in CDL — future: OSM power=line buffer keepouts.
- **Precision upgrade (commit ea1f87b):** boundaries now EDGE-EXACT (pixel-corner tracing, no 15m inset; rectangles → 4 corners). **In-field holes** (farmsteads/ponds/tree stands) traced per field → auto-keepouts through plan_multi (honored even if zone service down), rendered as red overlay + donut cut-outs. Despeckle fills 1px noise (can bridge 1px pinches between adjacent fields — delete+redraw if separation matters). Live: 9 holes in the Sabetha box; 351ac field planned w/ 3 holes → 0 waypoints inside. 77 tests.

#### Refinement audit (2026-07-13) — 20 defects fixed (commit e248499)
- 12-agent ultracode find→verify→fix sweep. Doubles as the pre-real-flight audit. 112 backend tests (was 77), both frontends green, flight fixes re-verified live.
- **FLIGHT (10, several HIGH — mattered for the real Cube):** long lock-held recv transactions dropped HEARTBEATs → watchdog killed a healthy link (fixed via _recv_blocking); ALL endpoints were `async def` running blocking pymavlink on the event loop → one slow call froze the whole API incl. emergency RTL/disarm (verified /health 7.4s→0.22s); `/mode LAND` (not an ArduPlane mode) returned 200 while flying → now 400; /mode //disarm //mission-start now report the real ACK; auto-reconnect overrode operator disconnect; RC-override WS died silently on one bad frame; socket leak on no-heartbeat; mission-upload wrong-seq on retransmit; unlocked concurrent MAVLink sends. Regression suite: test_flight_fixes.py.
- **PLANNING (3):** speed=NaN 500→422; keepout CPU-abuse bound; Douglas-Peucker no longer shrinks no-spray holes inward (holes stay exact).
- **GCS UI (6):** transient mid-flight link loss no longer fakes a disarm; stale-plan guard; leaflet layer/cursor follow; homeRef not latched from log playback; in-flight RTL/LAND/DISARM surface failures; manual-control WS cleanup.
- **CUSTOMER (1):** duplicate consecutive vertex no longer falsely rejected as self-crossing; date validation uses business (Central) tz.

#### Directive adoption + M1a observability (2026-07-22/23)
- **ARCHITECTURE.md adopted** (user-provided UAV engineering directive, saved verbatim w/ interpretation notes; airframe decision: ArduPlane primary, copter-ready abstractions). **GAP-ANALYSIS.md**: 3-agent line-level audit vs directive — strong mission protocol/ACK/heartbeat plumbing; 4 hardware-blocking gaps (STATUSTEXT dropped, unverified REAL32 param writes, zero app logging, sysid 255 unfiltered) → roadmap M1–M7.
- **Single-file exe shipped first** (`backend\dist\AgOpsGCS.exe`, 49MB): PyInstaller bundle serving built frontend; `start-all.ps1` launcher; frontend URLs unified in `src/api.js`; **sim router** (`/api/sim/start|stop|status`) spawns bundled SITL from the UI Simulator button — readiness probe must be netstat-passive (a connect-probe kills this SITL build). Exe validated end-to-end: double-click → SITL auto-spawn → connect → 3D fix.
- **M1a SHIPPED**: `app/eventlog.py` (JSONL `logs/events/`, 500-ring, `/api/logs/events`); vehicle_manager instrumented (STATUSTEXT ring→telemetry, EKF_STATUS_REPORT→`ekf_healthy`, SYS_STATUS health bits→`sensor_errors`, `link_quality`+`last_heartbeat_age`, thread-safe `snapshot()` now the only API read path, `_handle_msg` extracted testable). 133 backend tests (21 new; httpx added for TestClient). SITL-validated live: EKF STATUSTEXT stream, ekf_healthy False→True over GPS convergence, full link/mode/arm/disarm audit trail. **M1b now shipped (see below).** Exe NOT yet rebuilt with M1 (rebuild after M1b).

#### M3 parameter engine (2026-07-24) — SHIPPED, SITL-validated
- **Param cache** (`name → {value, type, index}`) in `vehicle_manager`, guarded by its own lock, kept live by every verified (M1b) write and by the sync. `get_cached_params()`, `cached_type()`/`cached_value()`.
- **Background full sync on connect** (`sync_params()` → `get_all_params()` in a thread; the SYNCHRONIZING work, decoupled from READY so a slow radio never delays flight-readiness). **Missing-index gap-fill:** after the bulk list, re-requests any indices the stream dropped, individually by index (3 rounds). Progress in `snapshot()['param_sync']` = `{syncing, received, total, synced}`. Guards out ArduPilot's `param_index=65535` "not-in-list" sentinel so the count can't exceed total.
- **Metadata validation** (`app/param_meta.py`): rejects non-finite, non-integer values for INTx params (using the **cached MAV_PARAM_TYPE**), out-of-int-type-range, and out-of-curated-range writes — **before** anything hits the wire. Curated ranges cover the params the app writes (fence/failsafe/identity/WP_RADIUS), set no narrower than the UI's pydantic bounds. `POST /vehicle/params` returns **422** on rejection (distinct from the 502 link-failure). Full apm.pdef.xml ingestion deferred.
- **`set_params_atomic`** — all-or-nothing batch: validate everything first (reject → send nothing), then apply in order; on any mid-batch verify failure, **roll back** the already-applied params to their previous values. **Geofence + failsafe endpoints now use it** (`_raise_if_not_applied` → 422 reject / 502 rolled-back). Change tracking: `set` logs previous→accepted; `atomic_ok`/`atomic_rollback`/`set_rejected` events.
- New endpoints: `GET /vehicle/params?cached=true` (instant, no link hit) + `POST /vehicle/params/sync`. ParamsPanel prefers the cache, falls back to a fresh sync, and surfaces 422 validation messages.
- **174 backend tests** (+14, `tests/test_m3_params.py`; the shared `FakeParamConn` grew list/index/gap-fill modeling). GCS frontend builds.
- **SITL-VALIDATED (ArduPlane 4.8.0):** background sync of **1430 params** (synced=True), **gap-fill fired live** (`sync_gapfill missing=1` recovered a dropped index); cached read **0.07s**; **422** on FENCE_RADIUS=999999 (range) and on FENCE_ACTION=2.5 (fractional int — caught via the cached INT8 type); atomic geofence apply (radius 1900/alt 110) verified + read-back-confirmed on the FC; `atomic_ok` + `set_rejected` in the event log. (Rollback path is unit-tested — SITL accepts valid writes, so a mid-batch failure can't be forced there.) Cosmetic count-guard fix (excludes ArduPilot's 65535 sentinel from the received count) is committed in `256bbed`; validated live only for the pre-fix count discrepancy, the fix itself is unit-tested.
- Committed + pushed as `256bbed`; **`AgOpsGCS.exe` rebuilt with M1+M2+M3** (smoke-tested: serves app, `gcs_sysid:252`, cached-params route present).
- **Next: M4 — SITL scenario harness** (reproducible fault-injection scenarios: link-loss, gps-failure via SIM_GPS_DISABLE, battery-fault, rtl-recovery; CI-able integration tests).

#### M2 link & identity (2026-07-24) — SHIPPED, SITL-validated (closes last hardware-blocking finding)
- **GCS identity moved off the colliding default.** New `app/config.py` (first real config surface): `GCS_SYSID` default **252** (env-configurable, NOT 255 so we don't collide with Mission Planner on a shared endpoint), `GCS_COMPID`, `MANAGE_SYSID_MYGCS`. Connection now created with our `source_system`/`source_component`.
- **RX sysid filtering** (`_from_vehicle`): once the vehicle's sysid is locked from its first heartbeat, the telemetry loop **and** `_recv_blocking` drop traffic from any other system — a co-connected Mission Planner (255) or another vehicle can't feed our heartbeat watchdog or steal our PARAM_VALUE/COMMAND_ACK. `msgs_filtered` counter in snapshot.
- **Connection state machine** (`LinkState`): DISCONNECTED→CONNECTING→WAITING_HEARTBEAT→SYNCHRONIZING→READY↔DEGRADED→LOST, each transition logged (`link/state` from_state→to_state) and exposed in snapshot/WS + `/connection/status`. **Graded `link_level`** (good/nominal/degraded/poor/critical from heartbeat age); READY→DEGRADED at 3s, →LOST at the existing validated 5s watchdog (kept 5s deliberately; 10s is a display level only).
- **AUTOPILOT_VERSION** fetched on connect → decoded capabilities (mission_int/command_int/ftp/…, fw_version) in snapshot. **BATTERY_STATUS** → `battery_consumed_mah` + better remaining estimate.
- **SYSID_MYGCS auto-align (the safety-critical piece).** Moving off 255 would make ArduPilot's FS_GCS ignore our heartbeats and RTL (the exact historical bug) unless the vehicle's commanding-GCS param points at us. On connect we read → verified-write (M1b) that param to our sysid. **Firmware-aware:** the param was renamed `SYSID_MYGCS`→`MAV_GCS_SYSID` in ArduPilot 4.5+; we try `MAV_GCS_SYSID` first, fall back to `SYSID_MYGCS` (discovered live — the SITL is 4.8.0, so only the new name exists).
- **160 backend tests** (+13, `tests/test_m2_link.py`). Frontend: TopBar link chip shows the state + level (colored dot) with an identity/capability/fw tooltip.
- **SITL-VALIDATED (ArduPlane 4.8.0):** clean state-machine transition log on connect; capabilities fetched (mission_int/command_int/ftp true, fw 4.8.0); `MAV_GCS_SYSID` aligned 255→252 verified; BATTERY_STATUS live; RX filter dropped **944** non-vehicle msgs (SITL's `SIM_SHIP_SYSID=17` sim ship) while all sysid-1 telemetry passed; **armed AUTO takeoff held 40m for 24s with mode never leaving AUTO — no FS_GCS RTL**, proving the new sysid is recognized. RTL command then honored. (Shared-endpoint co-GCS filtering can't be reproduced on SITL's separate ports — unit-tested instead.)
- **`AgOpsGCS.exe` rebuilt 2026-07-24** with M1+M2 baked in (`cd backend && venv\Scripts\pyinstaller.exe --clean --noconfirm AgOpsGCS.spec` after `cd frontend && npm run build`; output `backend\dist\AgOpsGCS.exe` ~51MB, gitignored). Smoke-tested: `gcs_sysid:252` on `/connection/status`, events + sim routers present, React app served.
- **Next: M3 — parameter engine** (cache + full sync on connect during the SYNCHRONIZING phase, metadata/range validation, batch+rollback, change tracking).

#### M1b param echo-verification (2026-07-24) — SHIPPED
- **Verified parameter writes.** `vehicle_manager.set_param`/`set_params` no longer fire-and-forget: read the param's real `MAV_PARAM_TYPE` → `PARAM_SET` with that type (not a hardcoded REAL32) → wait for the vehicle's `PARAM_VALUE` echo (read-back fallback if the echo drops on a lossy radio) → compare (int params exact, float params within float32 precision) → return `{verified, accepted, previous, param_type}`. Up to 3 set-verify attempts; tunable `_PARAM_TIMEOUT`.
- **Routers fail loudly.** POST `/vehicle/params`, `/safety/geofence`, `/safety/failsafe` now return **502 with the failed param name(s)** when the FC doesn't confirm — no more unconditional `"ok"`. This closes gap-analysis hardware-blocking finding #2 (an operator could believe a fence/failsafe was set when it wasn't).
- **UI tells the truth.** SafetyPanel names the rejected param ("Geofence NOT set — vehicle rejected FENCE_RADIUS") and re-reads after apply; ParamsPanel shows the value the FC *actually accepted*, not the requested one.
- **147 backend tests** (14 new in `tests/test_m1b_params.py`, faithful ArduPilot-param-store fake: typed values, spontaneous echo, clamp/reject, dropped-echo). Both frontends build green.
- **SITL-VALIDATED LIVE (2026-07-24)** against real ArduPlane (GPS fix, EKF healthy): geofence batch write of mixed INT8 (`FENCE_ACTION`=6, `FENCE_ENABLE`=0) + REAL32 (`FENCE_RADIUS`=1750, `FENCE_ALT_MAX`=90) → `verified:true`, and a re-read confirmed the values actually changed on the FC; single float (`WP_RADIUS`=45) and int (`FS_GCS_ENABL`=1) writes verified; **unknown param `BOGUS_XYZ` → HTTP 502 `"no PARAM_VALUE echo from vehicle"`**; every write (successes + the rejection) landed in the M1a event log with its accepted value.
- **Timing tuned from the live run:** a rejected/unknown param originally took ~14s (2s type-read + 3×(2s echo + 2s read-back)). Cut `_PARAM_TIMEOUT` 2.0→1.0s and `_PARAM_SET_ATTEMPTS` 3→2 (ArduPilot echoes a PARAM_SET almost instantly), bounding the worst case to ~5s. Valid-param writes were already fast (echo arrives immediately). **This tune landed AFTER the running backend was started, so it's on disk but was NOT itself exercised in that SITL session — re-validate the ~5s worst case on next SITL run (or just trust the constant change).**
- **Next: M2 — link & identity** (connection state machine, sysid config, RX filtering). Rebuild the exe with M1 whenever convenient.

#### Docs & dev-experience pass (2026-07-22)
- Added `start-all.ps1` — one-command launcher (SITL + backend + GCS + customer site, each own window, auto-opens browser). Root cause: previously required manually running 3-4 commands across windows in the right order every session.
- Rewrote `README.md` to be accurate and demo-ready (was still referencing dronekit and `--reload`).
- Restructured this file: stable/current reference stays above the `---`; dated session logs moved into this **History** section so the living doc doesn't grow unbounded. Add new dated entries here going forward instead of appending to the top.

## Preferences
<!-- Workflow preferences, tools, conventions -->

## Notes
- Git configured globally: taborbachelor@gmail.com / "Tabor Bachelor"
- Node.js v24.18.0 installed at C:\Program Files\nodejs (needs PATH export in bash)
- Python 3.13 at C:\Users\tabor\AppData\Local\Programs\Python\Python313

# CLAUDE-CALEB

## User
- Name: Caleb (Tabor Bachelor)
- GitHub: taborbachelor
- Email: taborbachelor@gmail.com

## Projects

### Relevyn
- Status: In progress
- Location: See **CLAUDE-RELEVYN.md** (`~\CLAUDE-RELEVYN.md`) — full context lives there
- Description: AI brand-visibility SaaS + X/social marketing automation — completely separate from RC Plane project

### RC Plane GCS (Friend's Project)
- **VISION:** an autonomous **field-spraying drone business**. Customer website (order → select field → schedule → pay via Stripe → confirmed + updates) + auto field selection w/ boundary snap + auto coverage flight paths + terrain intelligence (field/tree/water recognition & avoidance). GCS = operator side, website = customer side. Reality flags given to user: needs Stripe account, and commercial spraying = FAA Part 137 + chemical licensing (their homework, not blocking software).
- Status: **Full ag-ops platform + directive milestones M1–M3 shipped, all SITL-validated** (last commit `256bbed`, 2026-07-24). Ag platform: GCS + customer website + zone-aware multi-field job planner + USDA imagery field auto-detection + 20-defect refinement audit. Directive M1–M3: observability/event-log, verified param writes, link identity (sysid/state-machine/RX-filter), parameter engine (cache/sync/validation/atomic). **All 4 hardware-blocking findings cleared.** Remaining work is hardware-gated (Cube bench test, Stripe keys, powerline data), roadmap M4+, or new ideas.
- Location: `~\rc-plane-app` (custom app). **Paths in this file are profile-relative** — the working machine has changed at least once (was `C:\Users\tabor`, was `C:\Users\jacks` as of 2026-08-18); never hardcode a profile name.
- GitHub: https://github.com/taborbachelor/caleb-rc-project (branch `main`) — THIS is the project repo
- Reference: `~\rc-plane` (Mission Planner fork, read-only reference; separate repo taborcaleb — public). NOT cloned on every machine; re-clone from GitHub if a machine lacks it.
- Description: Custom ground control station for a friend's 3D-printed RC plane/drone
- UI Style: DJI Agriculture UI/UX — futuristic, HUD overlays, glassmorphic panels
- **Engineering standards: `rc-plane-app\ARCHITECTURE.md`** — UAV architecture directive adopted 2026-07-22 (professional ArduPilot/MAVLink/Mission Planner practices; read before structural changes; report changes against its final-instruction checklist)
- Note: Completely separate from Relevyn

> ### ▶ RESUME HERE (start of next session)
> **🟢 TWO SESSIONS? READ `LANES.md` FIRST** (repo root, git-tracked) — the parallel-work board:
> lane ownership by file, the SITL port lock, the seam register, and the append-only decisions log.
> Claim a lane there before touching anything, and read/write it by its ABSOLUTE main-checkout path
> even from a worktree. It exists so two sessions stop re-learning the `86c6a6e` cross-lane bug.
>
> **Mission context: airframe fully printed, internals imminent. Backend flight-readiness (A1–A5), UI overhaul (B1–B3), a full-backend hardening audit, AND the guardian safety-monitor expansion are ALL SHIPPED and on `main`.** Remaining pre-first-flight work is one eyeball check plus hardware-gated items; everything else is new feature work off the roadmap below.
>
> **State as of 2026-08-18 end of session (commit `86c6a6e`, working tree clean, EVERYTHING PUSHED — `main` local == `origin/main`; no other branches, no worktrees, no stashes):**
> - **Backend:** M1a/M1b/M2/M3/M4 + guardian + preflight gate (**M6 now COMPLETE**) + bench kit + soak, the 2026-08-15 hardening pass (~35 audit findings), Lane B's powerline keepouts + connector-leg rerouting + coverage analysis, and **Lane A's full spray-flight safety set** (bank angle, wind, live keepout proximity, post-flight scorecard, alert unification). **379 unit tests + 14 live SITL scenarios + 16 frontend tests green**, verified on `main` after both lanes merged (`pytest` / `backend\scenarios.ps1 all`, ~5 min).
> - ⚠️ **The SITL suite is green ONLY when this machine is quiet — it is not safe to run two sessions against it.** Verified 2026-08-18: **12/12 green in 4:29** (10 existing + 2 new) on an idle machine, matching the documented ~4 min. But three earlier runs that overlapped a second Claude session failed 3, 4 and 5 scenarios with a DIFFERENT set each time and degraded 350s → 490s → 802s. Failures are connection-level (`no heartbeat from vehicle`, WinError 10061/10054) — a scenario connecting to a SITL that hasn't finished dying — never assertion failures. Cause: port 5760 is single-occupancy, `conftest.py` teardown waits on `/api/sim/status` + a flat 1 s without ever proving TCP 5760 is free, and `sim._running()` treats a listening port as "already running". **So: a red scenario during parallel work is far more likely contention than a real regression — re-run it alone before believing it.** The durable fix is Lane D item 3 (parameterize `harness.py`'s hardcoded `tcp:127.0.0.1:5760`) plus a teardown that waits for the port to clear.
> - ✅ **Lane A MERGED to `main` 2026-08-18** (`b812ff9`, `2e47db8`, `b8f1f6e`, fast-forward): guardian Part 3C scenario proof (EKF-variance + airspeed-stall live, two new sim faults; **vibration proven UNPROVABLE on this SITL** and deliberately given no fault endpoint), the bank-angle monitor, wind telemetry, the live keepout-proximity monitor, the post-flight scorecard, and the alert-threshold unification (last M6 slice). Verified on `main` post-merge. Pushed 2026-08-18; the `lane-a/guardian-proof` branch and the `.claude/worktrees/lane-b` worktree were verified clean + fully merged and **deleted**.
> - ✅ **Lane B MERGED + PUSHED 2026-08-18** (`e480c35`, `0a69ee6`, `401f9c2`, `86c6a6e`): powerline keepouts (OSM `power=line`/`minor_line` corridors, hazard-rendered), **connector-leg rerouting** around hazards (`app/reroute.py` — convex-hull taut paths, bounded detours, segment ordering that turns ten crossings into one), a **fail-closed 409 on unresolved hazard crossings**, **coverage analysis** (`coverage_pct` / `sprayable_acres` / `uncovered_acres`), and the cross-lane fix below.
> - 🔴 **THE CROSS-LANE BUG — read this before splitting work by file again.** Lane A's live keepout-proximity monitor was built end to end (`keepout_watch.py`, endpoints, guardian wiring, tests, a SITL scenario) and **nothing ever called `POST /api/safety/keepouts`** — it ran with zero rings and could never warn. The backend half was Lane A's, the UI half (`SprayPanel.jsx`) was Lane B's, and the seam was owned by nobody. Both lanes were individually green and individually "done". Fixed in `86c6a6e`: upload arms the monitor with the plan's zones + the operator's ACTUAL buffer, and says so loudly when arming fails. **Do a deliberate seam pass at the end of every parallel session** — endpoints with no caller, UI reading fields nothing sets, defaults on both sides that must agree.
> - 🔥 **Next highest-value safety item, surfaced by that work:** the aircraft banks **50-65 deg in ordinary loiter/RTL turns** (measured), past `ROLL_LIMIT_DEG`, while a real spray pass flies at **10-25 m** — where a 60 deg bank has no recovery altitude. Detection now exists; the fix is a **turn-geometry bank constraint in `coverage.py`** so the planner stops commanding them.
> - **UI:** Vite (1s builds), 3D FLY view default (CesiumJS, key-free, attitude-true aircraft, CHASE/ORBIT/FREE cams; 2D forced for planning/drawing), NavRail progressive disclosure, server preflight verdicts + guardian chip/annunciators, SprayPanel zone-failure opt-in + overflight warnings, hazard-crossing opt-in, coverage %, and the proximity-monitor arming status. **16 UI tests green.**
> - 💰 **Billing + valuation now live in `VALUATION.md`** (repo root, added 2026-08-19). Cost ledger, labour ledger, three valuation frames with their inputs, the milestones that move the number, and the terms to ask Caleb for. Update it with **`py tools\session_cost.py --new`** at the end of any working session — do not hand-parse transcripts, and do not start a second table anywhere. Current state: token basis **$1,767**, all-in cost (labour included) **≈$8,500**, replacement cost **$180k–340k**, recommended ask **$7,500** + retainer + per-acre royalty. **Nothing has been invoiced since 2026-08-14** — everything from 08-15 onward is unbilled, and no agreement with Caleb exists yet. That conversation is the open item, not the arithmetic.
> - 🔴 **`AgOpsGCS.exe` DOES NOT EXIST on this machine.** (It was built 2026-08-15 on the previous machine — 53.7MB, smoke-tested — but that binary is not here, and it predates everything since the hardening pass anyway.) **A Cube bench day right now would have nothing to run.** Rebuild per README: `cd frontend; npm run build` then `cd backend; .\venv\Scripts\pyinstaller.exe AgOpsGCS.spec --noconfirm`. Note the kill-by-name gotcha — the onefile bootloader spawns a child, so killing the launched pid leaves the server on :8000.
>
> **Next-work menu:**
> 1. **3D scene eyeball check (user's eyes required):** run `start-all.ps1`, FLY view, confirm the Cesium scene renders and the aircraft's nose leads its trail — if it flies sideways, adjust the heading offset in `MapView3D.jsx`'s `oriProp`.
> 2. **Real Cube bench day** whenever hardware returns (Caleb's telemetry radio + receiver still pending): follow the `bench` scenario sequence — params backup → surface/servo tests → calibrations → first-flight bundle `POST /api/bench/first-flight-params {cells, apply:true}`. Data-USB → COM (115200); **PROP OFF; flight battery only after.**
> 3. ~~**Turn-geometry bank constraint in `coverage.py`**~~ **DONE 2026-08-19** (session bravo, PLANNER lane). The planner stopped turning onto the adjacent pass: it flies every Nth line and fills the gaps on later sweeps so each reversal has `2·R_min` of room at the planned speed. **Coverage is bit-identical — only the flight ORDER changes** — and the cost is path length: **73.2° → 25.3° of commanded bank on a 40-acre Sabetha-shaped field, at +38% flight distance** (+19% on 400×200 m). Default ceiling `coverage.DEFAULT_MAX_BANK_DEG = 25.0`; `max_bank=0` on either coverage endpoint restores the old serpentine. **The number that reframes the whole finding: the old adjacent-line plan demanded 73°, which the airframe cannot fly — so the measured 50-65° was the autopilot saturating its roll limit against an unflyable plan, not a tuning problem.** Every plan now reports `stats.turn_bank_deg` / `turn_bank_ok` / `turn_radius_m` / `turn_max_speed_ms`; a field too narrow to meet the limit gets the widest turns available plus `turn_bank_ok: false` and the speed that WOULD meet it. **Open follow-ups:** nothing renders those numbers yet (LANES seam **S5**, UI lane), and the 25° vs guardian's 31.5° low-altitude threshold pairing is **seam S2, proposed by bravo and awaiting AIR**. Full writeup: `SPRAY-FLIGHT-SAFETY.md` closing section. The 100 m `CoverageRequest.alt` default is still a known-wrong placeholder — untouched here on purpose, it is seam **S3** (AIR + PLANNER jointly).
> 4. **Headlands** — measurably justified now, not a guess: coverage analysis puts **0.41 and 0.56 acres of genuinely missed ground** on real 40-acre Sabetha fields with keepouts (100% on a clean field). That strip alongside a keepout is what perimeter passes close, and `coverage_pct` is the number that will verify the fix.
> 5. **SITL harness port fix (Lane D item 3)** — `tests/sitl/harness.py` hardcodes `tcp:127.0.0.1:5760`; teardown never proves the port is free. This is why parallel runs fail with a different scenario set each time. 14 scenarios now ride on it.
> 6. **Rebuild `AgOpsGCS.exe`** — see the red note above; a bench day currently has nothing to run.
> 7. **Post-flight scorecard UI** — small, unclaimed, same seam class as the keepout-arming bug: the backend writes a scorecard on every disarm and serves it, but `LogsPanel.jsx` never shows it. Min hazard distance, min RTL margin, max bank, warning counts per monitor.
> 8. Still not started: M5 mission model/resume, M7 layer restructure (`vehicle_manager.py` is now 2,057 lines — exclusive-lock job, run it solo), customer-site 3D field preview, Stripe keys (Caleb), pump-sensing answer (Caleb — gates spray verification entirely).
>
> **Get running — one command:** `.\start-all.ps1` from `rc-plane-app\` (see **Quick start** below). First run needs deps installed once (`pip install -r requirements.txt`, `npm install` ×2) — see the script's header comment or README.md.
>
> **Flagship demo (30 seconds):** SPRAY → Area → box some farmland near Sabetha → **Detect fields in area** (USDA traces the real fields, crop-labeled) → Generate Spray Plan (whole job: spray + orange transits + purple home legs) → Upload → FLY view → ARM & TAKEOFF.
>
> **Commit lineage** since the ag-platform baseline `e248499`: `2d2bc84` (M1a+exe+M1b) → `6fcf8d9` (M2) → `256bbed` (M3) → 2026-08-14 series: `8d3d629` (M4 harness) → `543400b` (guardian) → `eef43d8` (preflight gate) → `517d53f` (bench kit) → `bfd4da7` (soak) → `8e9de0c` (B1 Vite) → `9b50002` (B2 3D view) → `ba10eda` (B3 redesign) → `7bb3f60` (backend hardening) → `94c4d76` + `c401628` (guardian EKF/vibration/airspeed monitors, merged from the worktree branch) → `b46350a` (hand-off design docs) → `b0122e6` (track CLAUDE-CALEB.md) → `43440d5` (README exe/Python notes) → 2026-08-18: `b6e7374` (doc reconciliation) → `79d54a4` (powerline keepouts) → `e480c35` (connector-leg rerouting) → `0a69ee6` (fail-closed hazard crossings) → `401f9c2` (coverage analysis) → `b812ff9` + `2e47db8` + `b8f1f6e` (Lane A: scenario proof, spray-flight safety set, keepout proximity) → `66c2c25` (Lane A doc merge) → `86c6a6e` (arm the proximity monitor, HEAD).
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
├── VALUATION.md                 # cost ledger + what the software is worth + terms — SINGLE SOURCE
├── tools/
│   └── session_cost.py          # refreshes VALUATION.md's ledger from Claude transcripts
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
- 🔴 **NEVER junction (`mklink /J`) `node_modules` — or any shared directory — from a git worktree into the main checkout.** Deleting the worktree follows the junction and wipes the TARGET's contents. This happened 2026-08-18: a worktree needed frontend deps, a junction was created to the main checkout's `frontend/node_modules`, and removing the worktree left the main checkout with an empty `node_modules` — `npm test` and `npm run build` both dead until `npm install` was re-run. A worktree that needs node deps should get its own `npm install` (~9 s here), or use `mklink /D` only if you also delete the link with `rmdir` (never `rm -rf`). The same hazard applies to the `sitl/` binaries, which were COPIED rather than linked for exactly this reason.

#### Still TODO (reordered 2026-08-18)
- ~~**BLOCKING QUESTION** about spray altitude~~ **ANSWERED 2026-08-18: a real spray pass flies
  10–25 m AGL.** `CoverageRequest.alt` still defaults to **100 m**, i.e. ~4-10x the real operating
  altitude — that default is now a known-wrong placeholder to revisit, not a validated setting.
  Consequences: at 10–25 m the aircraft is INSIDE the wire-strike envelope (rural distribution
  lines ~8–12 m, transmission ~20–45 m), so powerline keepouts + connector-leg rerouting are
  airframe survival rather than spray quality; bank-angle (#4) and terrain/AGL (#8) move up,
  because a stall-spin at 15 m has no recovery altitude.
- **Pump sensing: NOT DECIDED YET** (asked 2026-08-18). Spray verification stays out of scope
  until it is — on a PWM-only path, "verification" can only ever be inferential, and shipping
  something that looks like confirmation would be worse than shipping nothing. Re-ask.
- **3D scene eyeball check** (needs the user's eyes, still open): run `start-all.ps1`, FLY view,
  confirm the Cesium scene renders and the aircraft's nose leads its trail; if sideways, adjust the
  heading offset in `MapView3D.jsx`'s `oriProp`.
- **Software, ready to start now (all specced, repo quiet, nothing blocking):**
  1. ~~**SITL scenario proof for the three merged guardian monitors**~~ **2/3 DONE 2026-08-18**
     (`SPRAY-FLIGHT-SAFETY.md` Part 3C). EKF-variance and airspeed-stall now have live scenarios
     (`.\scenarios.ps1 ekf-variance` / `airspeed-stall`) driven by two new verified-write faults
     (`gps_noise` → `SIM_GPS1_HNSE`, `airspeed` → `SIM_ARSPD_FAIL`). **The vibration monitor
     CANNOT be driven from this SITL build** — nine SIM_* knobs tried across five flights, every
     one verified as written, none moved the VIBRATION levels off ~0.17 m/s/s against a 30 m/s/s
     threshold (`SIM_VIB_MOT_*` models multicopter motor vibration; a plane frame has no motors in
     that list). It stays unit-tested only, and deliberately has NO fault endpoint — one that
     wrote no-op params would report "fault injected" on an unaffected vehicle. Full evidence, and
     the two near-misses that almost produced a green test proving nothing, in Part 3C.
  2. ~~**Live keepout-proximity monitor**~~ **DONE 2026-08-18** — `app/keepout_watch.py` +
     guardian `keepout` monitor + `POST /api/safety/keepouts` + `keepout-prox` scenario.
  3. ~~**Powerline keepouts**~~ **DONE 2026-08-18** (Lane B, `79d54a4`).
  4. ~~**Post-flight scorecard**~~ **BACKEND DONE 2026-08-18** — written on disarm, served on
     `GET /api/logs/{name}` (+ `has_scorecard` in the list view). ⚠️ **NO UI YET** —
     `LogsPanel.jsx` does not render it, so the operator cannot see a scorecard without hitting
     the API by hand. This is the SAME seam class as the keepout-arming bug (`86c6a6e`): a
     backend surface with no caller. Small, unclaimed, and worth doing next time the frontend is
     open — min hazard distance, min RTL margin, max bank, warning counts per monitor.
  5. ~~**Bank-angle monitor** (#4) and **wind** (#6)~~ **DONE 2026-08-18.** Wind IS streamed under
     the existing `MAV_DATA_STREAM_ALL` request — verified live, no extra subscription needed.
  6. **🔥 Turn-geometry bank constraint in `coverage.py`** — NEW, and now the highest-value
     safety item in the project. Measured 2026-08-18: **this airframe banks 50-65 deg in ORDINARY
     loiter/RTL turns**, past `ROLL_LIMIT_DEG`, while a real spray pass flies at 10-25 m where a
     60 deg bank (stall speed +41%) has no recovery altitude. The new in-flight monitor makes this
     visible but cannot make a turn gentler — the planner has to stop commanding them.
  7. **M5** mission model/persistence/resume. (Connector-leg rerouting around keepouts is
     **DONE** — Lane B, `e480c35`.)
  8. **M7 layer restructure** — `vehicle_manager.py` is now **~2,000 lines** (was 815 when the
     directive was adopted). Biggest architectural debt in the repo; strangler moves only.
  9. ~~**Alert-threshold unification**~~ **DONE 2026-08-18** — `AlertCenter` renders guardian
     verdicts and holds no in-flight thresholds of its own.
  10. Config management (still ABSENT per `GAP-ANALYSIS.md`); customer-site 3D field/flight preview.
- **Ops / verification items, small and low-collision (surfaced 2026-08-18):**
  - **`AgOpsGCS.exe` does not exist on this machine** and the last built exe predates the guardian
    monitor merge — a Cube bench day would have nothing to run. Rebuild per README (frontend bundle
    first, then PyInstaller), after any in-flight backend work lands.
  - **Mission Planner compatibility checklist (`ARCHITECTURE.md`) is largely unverified.** M2's own
    note says SITL's separate ports (5760/5762) cannot reproduce a *shared-endpoint* co-GCS
    collision, so cross-GCS sysid filtering is covered by unit tests only. Needs a real co-connect
    test and a run through the ten-item checklist.
  - **`backend/tests/sitl/harness.py` hardcodes `tcp:127.0.0.1:5760`.** That is what makes SITL
    single-occupancy and blocks two sessions from running scenarios concurrently. Parameterizing it
    is a small change that unblocks real parallel work.
  - ~~Connector-leg rerouting~~ **DONE 2026-08-18** (`e480c35`, `0a69ee6`). Hazard legs are routed
    around; detours are BOUNDED (a real line runs kilometres past the field, so "around" it can
    mean a 5 km detour for a 50 m hop), and an unresolved crossing now REFUSES the plan (409)
    unless the operator explicitly opts in — a leg that still crosses is a path through the
    conductor, not a statistic. Verified against a live 115 kV line, which is how both of those
    were found; fixtures passed while the real thing planned a path 1.7 m from the wire.
  - ~~Coverage analysis~~ **DONE 2026-08-18** (`401f9c2`) — `coverage_pct` / `sprayable_acres` /
    `uncovered_acres` on every zone-aware plan, area-weighted into job totals and shown in the
    panel. Real Sabetha numbers: 100% clean, 98.8% / 98.0% with keepouts = **0.41 and 0.56 acres
    of genuinely missed ground**, which is the measurement that justifies headlands.
  - **Headlands — the clearest next planner feature, now with a number behind it.** Perimeter
    passes inset half a swath to close that boundary strip; `coverage_pct` verifies the fix.
  - **Seam pass at the end of every parallel session.** The 2026-08-18 cross-lane bug (a fully
    built proximity monitor that nothing ever armed) was invisible to both lanes because each was
    green on its own. Check explicitly for endpoints with no caller, UI reading fields nothing
    sets, and defaults on both sides that must agree.
  - **The "Kansas is flat" assumption is implicit in every guardian monitor.** `SPRAY-FLIGHT-SAFETY.md`
    #8 asks for it to be written down explicitly (e.g. `guardian.py`'s module docstring). Not
    hardware-gated; anyone can do it.
- **Parallelism note (2026-08-18):** the safety/telemetry work (`guardian.py`, `vehicle_manager.py`,
  `routers/safety.py`, `routers/logs.py`, `tests/sitl/`) and the planner/GIS/hazard-UI work
  (`gis_zones.py`, both coverage routers, `MapView*.jsx`, `SprayPanel.jsx`) are file-disjoint and
  can run as two concurrent sessions via `EnterWorktree`. **M7 and M5 cannot** — `vehicle_manager.py`
  is 1,866 lines with 63 functions and 8 of 10 routers import it, so any extraction or mission-model
  change collides with everything. Run those solo.
- **Hardware-gated:** Cube bench day (script = the `bench` scenario sequence: backup → surface/servo tests → calibrations → `POST /api/bench/first-flight-params {cells, apply:true}`); telemetry radio + receiver (Caleb's task); Stripe live keys (Caleb); WebRTC/HLS video (needs HD video hardware); terrain intelligence phase 2 (needs camera + companion computer); pump/spray-system verification (needs Caleb's answer on sensing).
- **Billing + valuation:** `VALUATION.md` at the repo root is the single source of truth — cost ledger, hours, what the software is worth, and the terms. Refresh with `py tools\session_cost.py --new`. Invoiced through 2026-08-14; everything after that is unbilled and no agreement is signed.

#### Design ideas (proposed, NOT implemented; user hasn't picked yet)
- Tier 1 (recommended as one "flight safety & feedback" release — mostly now shipped, see History "List-finish session"): pre-flight checklist card gating ARM (with override); aviation-style alerts/annunciators + optional voice callouts; post-flight summary card on disarm; "RTL margin" can-I-get-home indicator
- Tier 2: FPV mode (video fullscreen + HUD overlay, map becomes PiP — the showpiece, wants camera hardware); instrument cards w/ sparklines replacing bottom strip; flight-phase adaptive layout; mission altitude-profile ribbon
- Tier 3: day/night/field themes; map long-press radial menu; first-run tour; tablet layout

#### Billing — MOVED to `VALUATION.md` (2026-08-19)
> **The cost ledger, the labour ledger, what the software is worth, and the terms to ask Caleb for
> now live in `VALUATION.md` at the repo root.** It is the single source of truth; do not maintain
> a second table here. Update it with `py tools\session_cost.py --new` at the end of any working
> session — the script does the transcript parsing that used to be done by hand, and it separates
> sessions that were only partly on this project so a Relevyn evening never gets billed to Caleb.
>
> The table below is the historical record as of 2026-08-14 and is **frozen** — kept for provenance
> because those transcripts lived on a machine we no longer have. New rows go in `VALUATION.md`.

Original method note: Claude Code token-usage cost at Anthropic API list-price equivalent (not actual subscription cost — Claude Code runs on a Max/Pro plan, this is a billing proxy). Parse local session transcripts (`~/.claude/projects/.../*.jsonl`) filtered to `cwd` under `rc-plane`/`rc-plane-app`, sum `usage` tokens per model (input/output/cache-write-5m/cache-write-1h/cache-read), price at current API rates, include workflow subagent transcripts (`subagents/workflows/*/agent-*.jsonl`).

| Date logged | Period covered | Cost basis (API list-price) | +20% margin | Notes |
|---|---|---|---|---|
| 2026-07-21 | 2026-07-10 → 2026-07-13 (main session + 44 workflow subagents: ag-platform round 1/2, refinement-audit) | $1,182.69 | **$1,419.23** | Session `29330544-8377-4fcf-a93f-a4c0c39cc962`. Breakdown: main session opus-4-6 $5.12 + opus-4-8 $184.99 + fable-5 $705.36; subagents fable-5 $279.54 + opus-4-8 $7.68. |
| 2026-08-14 | 2026-07-21 → 2026-08-14 (4 sessions + subagents: docs/start-all pass, directive+M1a, M1b/M2/M3, and the big 08-14 both-tracks session) | $144.99 | **$173.99** | Sessions `58e2dfa5` (docs pass, sonnet-5 $4.62), `d2552655` (directive+M1a, opus-4-8 $14.02 + sonnet-5 $0.77), `81187eb4` (M1b/M2/M3, opus-4-8 $30.79 + sonnet-5 $0.23), `5c00e666` (M4→soak + UI B1–B3, fable-5 $92.50 + opus-5 $2.05). Excludes the still-open 2026-08-15 session (3D eyeball check + this billing update) — roll it into the next row. |

**Cumulative through 2026-08-14:** cost basis **$1,327.68**, with margin **$1,593.22**.
**Current cumulative (incl. everything since):** see `VALUATION.md` — **$1,767** basis / **$2,120** with margin,
and note that the number that actually matters is all-in cost (~$8,500, labour included), not this one.

---

## History (session-by-session build log)
Newest first isn't required — kept chronological. This section is an append-only record;
add a new dated entry here rather than editing old ones. Everything above the `---` is the
"living" reference — keep that current and reorganize freely.

#### Cross-lane gap: the live proximity monitor was never armed (2026-08-18)
- **Lane A built the live keepout-proximity monitor end to end** — `keepout_watch.py`,
  `POST/GET/DELETE /api/safety/keepouts`, guardian integration, telemetry plumbing, unit tests,
  a SITL scenario. **Nothing ever called `POST /api/safety/keepouts`.** The monitor ran with zero
  rings and could never warn. The backend half was Lane A's; the missing frontend half
  (`SprayPanel.jsx`) is a Lane B file, so it fell exactly between the two sessions.
- **Fixed here:** mission upload now arms the monitor with the zones the plan was built against
  and the operator's ACTUAL `powerline_buffer` (not the default). This is required by design, not
  an oversight of Lane A's — their endpoint docstring is explicit that mission upload deliberately
  CLEARS the monitor, because the aircraft can fly a mission the GCS never planned and pretending
  we know its keepouts would be worse than admitting we don't. The UI therefore has to re-arm it.
- **Failure is stated, never implied away:** if arming fails the upload status says the monitor is
  NOT armed and cannot warn in flight, rather than reporting a clean upload. Re-planning clears
  the armed state, since it no longer matches what the monitor holds.
- **Also fixed:** `NumField` used a `<span>`, so every number input in the spray panel had no
  accessible name — failing the project's own WCAG commitment ("proper `<label>` on all form
  inputs") and making the controls unreachable by screen reader. Now a real `<label>`.
- 3 new frontend tests (16 total), mutation-checked. 379 backend tests green on the combined
  Lane A + Lane B state.
- **Process lesson — the seam between parallel lanes is where features die.** Both lanes were
  individually green and individually "done". The integration between them was owned by nobody.
  When lanes are split by file, someone has to explicitly check the seams: a backend endpoint with
  no caller, a UI reading a field nothing sets, two defaults that must agree. Worth a deliberate
  pass at the end of every parallel session.

#### Coverage analysis + real-Sabetha planner validation (2026-08-18)
- **Validated the whole planner against REAL Sabetha zone data first** (the actual operating area
  and demo site), applying the day's lesson that fixtures lie. Three 40-acre fields around town:
  all plan cleanly, 20 passes each, keepouts applied 2 / 13 / 0 by location, no refusals or
  crashes. Zones there: water 29, buildings 184, trees 0, **powerline 0** (consistent with the
  earlier finding — the powerline feature is inert at the demo site). Core chain is healthy.
- **New: coverage analysis** (`_coverage_stats` in `coverage.py`) — `coverage_pct`,
  `sprayable_acres`, `uncovered_acres`, aggregated area-weighted into `plan_multi` totals and
  shown in the SprayPanel job summary. Answers the question pass counts and path length do not:
  *did we actually cover the field?* Keepout area is excluded from the denominator — not spraying
  a pond is the plan working, not a gap.
- **Real numbers on those Sabetha fields: 100% on the clean field, 98.8% and 98.0% on the two
  with keepouts — 0.41 and 0.56 acres of genuinely missed ground each.** That gap is the strip
  alongside a keepout, and it is exactly what headland passes (still unbuilt) would close. Measure
  first, then optimise: the number now exists to justify and verify that work.
- Cheap by construction: the analysis runs in the rotated frame where passes are horizontal, so a
  sample is an O(1) lookup against at most two pass lines rather than a scan over every segment.
  Sample resolution is tied to the swath and the grid coarsens on huge fields rather than burning
  the shared CPU budget; budget exhaustion drops the diagnostic instead of losing the plan.
- **Caught a contract I was about to break silently:** `TestLegacyRegression` deliberately pins
  the EXACT stats key set for a call without keepouts ("indistinguishable from the old planner").
  Adding coverage keys unconditionally broke it. Coverage is now gated on `keepouts is not None`,
  exactly like `keepouts_applied`/`n_segments` — every product path (plan_auto, plan_multi) passes
  keepouts so they all get it, and a bare `/plan` caller opts in with `keepouts=[]`. Breaking a
  deliberately-tested promise should be a decision, not a side effect of a diagnostic.
- 335 backend tests (+6), 6 frontend, builds clean.

#### Hazard crossings are now FAIL-CLOSED — found by testing against a real 115 kV line (2026-08-18)
- **How it was found:** everything in the powerline + rerouting work had been verified against
  synthetic fixtures. Running the full chain against REAL OSM data — the "6th & Golden-Tecumseh
  Hill" 115 kV Evergy transmission line near Topeka — produced a plan whose **closest approach to
  the live conductor was 1.7 m**, with 0 reroutes and 29 unresolved crossings. The fixtures all
  passed. **Fixture-only verification was not evidence the feature worked.**
- **Root cause 1 — the fixture was unrealistic.** It used a 400 m line beside a 400 m field, so a
  detour could cheaply round the line's END. Real transmission lines run for kilometres past the
  field: routing "around" one means flying to the end of the line. Measured offline, a 50 m hop
  became a **5 km detour**. Detours are now bounded (`_detour_budget_m` = max(250 m, 3x the
  straight leg)); beyond that the leg is reported unresolved instead of planning a path nobody
  would fly. `LongLineTests` pins the realistic geometry.
- **Root cause 2 — reporting a crossing is not enough.** An unresolved crossing means the plan
  contains a leg flying THROUGH the corridor. A red warning on a plan that will destroy the
  aircraft is weak. **`plan_auto` and `plan_multi` now REFUSE (409 `hazard_crossings`) when any
  leg still crosses a hazard**, unless the caller passes `allow_hazard_crossings=true` — exactly
  the posture the zone-service outage already takes with `allow_missing_zones`. SprayPanel shows
  the same style of explicit "Plan anyway — legs WILL cross the powerline" opt-in, with the
  practical advice: split the field along the line and spray each side as its own job.
- **Verified against the same live line afterwards:** the plan is now REFUSED with "29 connecting
  leg(s) cross a powerline corridor and could not be routed around". With the explicit opt-in it
  plans and reports all 29 (closest approach 0.3 m — which is exactly why it must be deliberate).
  That field is criss-crossed by several lines and genuinely cannot be sprayed without crossing;
  the correct product answer is to say so, not to hide it in a statistic.
- **329 backend tests**, 6 frontend, builds clean. A cached copy of the real Overpass payload was
  kept for offline debugging — the public endpoint rate-limits (429/504) after a few queries, so
  cache the payload rather than re-fetching in a debug loop.
- **Standing lesson for this project:** GIS features must be validated against real OSM data
  before they are called done. Synthetic rings are too well-behaved — they are compact, isolated,
  and conveniently sized, and every one of those three assumptions was false in the field.
#### Lane A COMPLETE — spray-flight safety monitors, scorecard, alert unification (2026-08-18)
Finishes every software item in `SPRAY-FLIGHT-SAFETY.md`. Only the two hardware-gated ones (pump
verification, terrain/AGL) remain open, and both are blocked on hardware, not on us.
Branch `lane-a/guardian-proof`, rebased onto Lane B's powerline + reroute commits.

**Shipped**
- **Bank-angle monitor** (item 4). Default 45 deg = ArduPlane's own `ROLL_LIMIT_DEG` default,
  pinned to the autopilot's limit rather than picked by feel. Tightens automatically below 30 m
  (x0.7) because that is where a stall-spin has no recovery room. Warn-only by default.
- **Wind** (item 6). `WIND`/`WIND_COV` parsed into `wind_speed`/`wind_direction`, in the flight
  log and scorecard. The doc asked whether ArduPilot streams it before assuming — **it does**,
  verified live (`SIM_WIND_SPD=12/DIR=270` -> telemetry tracked 0.1 -> 12.0 m/s at 270), no extra
  subscription needed.
- **Live keepout proximity** (item 5). New pure module `app/keepout_watch.py` + guardian
  `keepout` monitor + `POST/GET/DELETE /api/safety/keepouts`. Only HAZARD kinds (powerline) warn;
  water/trees/buildings distance is measured for the debrief but never annunciated. `known` is
  reported separately from `ok` so missing zone data can't render as a green tick. **Mission
  upload clears the cached rings** — stale rings from the previous field would read as a
  confident all-clear over unsurveyed ground.
- **Post-flight scorecard** (Part 3B). Written on disarm beside the flight log, served on
  `GET /api/logs/{name}`. Extremes start as `None` not 0 (a scorecard saying "0 m to the nearest
  powerline" when no rings were loaded would be a dangerous lie); warnings counted per EPISODE,
  not per tick.
- **Alert-threshold unification** (the last open M6 slice). `AlertCenter.jsx` now holds NO
  in-flight thresholds — it renders the guardian's verdicts. It previously carried its own
  battery-percent and GPS-fix rules, so the UI could disagree with the guardian (which judges
  battery on VOLTAGE and GPS on fix AND sat count). Guardian results gained `warning_items`
  (each warning tagged with the monitor that raised it) so the UI shows EVERY active warning with
  its own dismiss state — the old code rendered `warnings[0]` and hid the rest, which became a
  real defect the moment bank and keepout monitors could warn alongside something else. Client
  keeps only what the backend structurally cannot judge: LINK LOST/RECONNECTING (guardian
  verdicts freeze when telemetry stops), RTL ENGAGED, and a disarmed pre-arm pack advisory.

**Verification:** 366 backend unit tests, 14 SITL scenarios, 13 frontend tests. Two new live
scenarios: `bank-angle` and `keepout-prox`.

**The finding that should drive the next piece of work:** the bank monitor was built with a 35 deg
default, then re-based to 45 after measuring that **this airframe banks 50-65 deg during ORDINARY
loiter and RTL turns** — past `ROLL_LIMIT_DEG`. At the confirmed 10-25 m spray altitude a 60 deg
bank (stall speed +41%) has no recovery room. The monitor makes it visible but cannot make turns
gentler; the fix is the **planning-time turn-geometry constraint in `coverage.py`** (Lane B's
files). That is now the highest-value software item in the project's safety chain.

**One test bug worth remembering:** the bank scenario first failed comparing the guardian's
`roll_deg` (54.2) against a telemetry read taken moments later (51.0). The guardian ticks at 1 Hz
while the aircraft rolls continuously — cross-checks against live attitude have to compare a
RANGE over a window, never two instants.

**Applied the parallel session's real-OSM lesson to this work, and it paid.** The proximity
monitor was unit-tested entirely on synthetic squares, so it was re-validated against live
Overpass data: Sabetha 213 rings, Topeka 3,732 (3,679 buildings, one ring with 4,952 vertices).
That found a real bug — the ring cap was 400, so a dense area lost 90% of its geometry and the
reported nearest-keepout distance came from an arbitrary subset: **52.4 m at a point where the
true answer was 19.0 m.** Truncation was changing the answer, not just the runtime. Cap raised to
4,000 (full Topeka query = 3.5 ms/tick against a 1000 ms budget, so the old cap bought nothing),
and any real truncation now reports `keepout_complete: false` instead of quietly implying a
complete answer. Hazard rings were correctly never dropped, so the safety path was sound
throughout — but the debrief number was not. Synthetic fixtures would never have shown it.

#### 🔴 SITL scenario suite is INTERMITTENTLY FAILING on this machine (2026-08-18) — not a code bug
- **Symptom:** `scenarios.ps1 all` fails 2–4 scenarios per run, with a DIFFERENT set each time.
  Observed sets across four runs: `{bench, link_watchdog, preflight}`, `{link_watchdog,
  preflight, rtl_recovery, soak}`, `{field_test, link_watchdog}`, `{battery_fault,
  link_watchdog}`. Every failure is the same assertion, at `h.connect()` BEFORE the scenario
  does anything: `connect failed: 500 ... [WinError 10054] An existing connection was forcibly
  closed by the remote host`. `link_watchdog` failed in all four.
- **PROVEN not to be a code regression.** The suite was re-run with all in-flight work stashed,
  working tree clean at `79d54a4` — **the pristine baseline failed too** (battery_fault +
  link_watchdog). Supporting evidence: `connection.py`, `sim.py` and `vehicle_manager.py` import
  neither `coverage` nor `reroute`, so nothing in the planner work is in the connect path at
  all. The failing scenarios also pass when run in isolation (4/4 green, 130s).
- **Mechanism:** a stale `ArduPlane.exe` is left holding TCP 5760 (confirmed by `tasklist` /
  `netstat` after several runs). The next scenario's SITL cannot own the port, and the backend's
  connect lands on a socket that immediately drops. `backend/tests/sitl/harness.py` hardcodes
  `tcp:127.0.0.1:5760`, so there is no isolation between scenarios and no way to run two at once.
- **Workaround until fixed:** `Get-Process ArduPlane | Stop-Process -Force` before every run, run
  the suite with the machine otherwise IDLE, and re-run any failure in isolation before believing
  it. Do NOT run the unit suite or anything CPU-heavy alongside it — doing so stretched a run
  from 245s to 466s and broke a scenario that passes clean (that one was self-inflicted).
- **Real fix (promote this up the queue — it is now more than a parallelism nicety):** give the
  harness a dynamic/parameterised port instead of the hardcoded 5760, and make teardown REAP the
  spawned simulator (verify the process is gone and the port is free before the next spawn,
  rather than assuming `POST /api/sim/stop` succeeded). Until then, a crashed scenario silently
  poisons every subsequent run, and the suite reports what looks exactly like a code regression.
- **Note it passed 10/10 twice earlier the same day**, so the flake is timing/state dependent,
  not a permanent break. That is precisely what makes it dangerous: a green run is not evidence
  the problem is gone.

#### Lane B item 2 — connector-leg rerouting SHIPPED (2026-08-18)
- **The problem:** spray PASSES were clipped around keepouts, but the connecting legs never
  were — in-field hops, inter-field transits, and the home leg all flew straight through, and
  only the in-field ones were even counted (`keepout_overflights`). Transits and home legs were
  not checked at all. Acceptable while every keepout protected spray quality; a powerline made
  it a collision.
- **`app/reroute.py` (new, pure geometry):** hazard rings are reduced to their CONVEX HULL,
  expanded by the clearance buffer with a circumscribed 32-sided polygon, and legs are routed as
  taut paths around them. Over-conservative by construction (hull ⊇ ring), matching the
  codebase's standing "over-standoff errs safe" rule. Deliberately NOT a visibility graph —
  shorter is not the goal, provably clear is, and the degenerate cases are far easier to get
  right on convex hulls. **Unroutable legs return None and the caller keeps counting them**, so
  a leg we failed to solve is never silently presented as safe.
- **The hazard set is a SUBSET of keepouts.** Only `powerline` today. Rerouting around every
  pond and treeline would add flight time and battery for no safety gain — overflying those with
  the sprayer off costs nothing. Water/trees/buildings keep the cheap straight-line behavior.
- **Three things found by measuring rather than assuming:**
  1. **The first working version made the mission 2.6× longer** (waypoints 40 → 210) because a
     field bisected by a line alternates sides every pass and each crossing got its own detour.
     Fixed by ordering the spray sub-segments so same-side passes fly together: **one crossing
     instead of ten, 1.12× path length, 54 waypoints.** Ordering only engages when a hazard
     actually blocks a leg, so hazard-free plans keep the exact serpentine they always had.
  2. **The first hull geometry made the feature a no-op.** Spray passes are clipped at exactly
     the buffer distance, so their endpoints sit exactly on the hazard boundary — and a
     circumscribed octagon bulges ~8% past it, putting EVERY endpoint inside the hull and making
     every leg unroutable. It reported 10 overflights and rerouted 0. Fixed with a 32-sided hull
     (~0.48% bulge) plus an explicit metric tolerance; worst-case clearance shortfall is now
     under 10 cm on a 20 m buffer, far inside the GPS error this projection already accepts.
  3. **The home leg must NOT be rerouted.** The mission ends at the last field waypoint and the
     aircraft returns under autopilot RTL, which flies straight and knows nothing about our
     keepouts. Appending detour points there would fly the aircraft out to a detour vertex and
     then RTL straight back across the line — worse than not trying. The home leg's route is
     computed for display and `totals.home_leg_hazard` warns the operator instead.
- **UI: the backend now says what each leg IS (`leg_kinds` / `combined_leg_kinds`).** `App.jsx`
  had been inferring spray-vs-hop from index PARITY (waypoints in strict pairs). Detours insert
  waypoints, so parity breaks — and the failure mode is drawing a hop as a SPRAY leg, i.e.
  showing spraying over a keepout. The unknown-kind fallback was also `spray`; it is now `hop`,
  the conservative direction. Detour legs render in hazard yellow so the operator can see why
  the path bends.
- **323 backend tests** (+22 geometry incl. 600 randomized property cases proving a returned
  path never enters a hazard, +20 planner/router). 6 frontend tests, frontend builds.
- **Still open (deliberately):** a concave hazard is treated as its filled hull, so detours are
  longer than strictly necessary and a gap inside the hull goes unused. Accepted — see the
  module docstring.

#### Lane B item 1 — powerline keepouts SHIPPED (2026-08-18)
- **OSM `power=line` / `power=minor_line` buffer keepouts, end to end**, per
  `POWERLINE-KEEPOUTS.md` (that doc is now marked SHIPPED and carries the full detail).
  Backend: one added Overpass clause in the SAME round-trip, `powerline` classify branch with an
  `location=underground` exclusion, `_LINEAR_KEYS` generalizing the waterway corridor branch, a
  `powerline` zone bucket (always present), power relations skipped. Routers: `powerline_buffer`
  default **20 m** — lateral FLIGHT clearance, not spray drift, because this is the one keepout
  kind that protects the airframe rather than the crop. Frontend: hazard-yellow rendering in both
  maps, a buffer control, legend entry, and an always-on OSM-incompleteness warning.
  **281 backend tests** (+17, `test_zones_powerline.py`), 6 frontend tests, frontend builds.
- **Three defects found while implementing, all fixed in the same pass:**
  1. **The waterway corridor path had NO unit tests at all** — `_corridor_ring` and the linear
     branch shipped in the 2026-08-15 hardening pass uncovered, so the design doc's "test it like
     the existing waterway tests" had nothing to copy. Both are now covered.
  2. **`plan_multi`'s clip buffer was an unconditional `max()`** over every per-kind field (only
     `plan_auto` was presence-aware). Adding the wider powerline default there would have silently
     widened EVERY keepout in EVERY multi-field job by 5 m even with no line in the area. Now
     presence-aware, matching `plan_auto`; two tests pin it.
  3. **No-spray zones never rendered in the 3D view** — `MapView3D`'s zone loop read
     `poly.polygon` (the sprayFields shape) while zones arrive as `{kind, coords, tags}`, so every
     zone was silently skipped. Live whenever the operator finished drawing and the app returned
     to 3D. Fixed; corridors now draw as ground polylines (a zero-area ring renders nothing as a
     polygon) and get real stroke weight in 2D as well, where ditch corridors were 1px and
     effectively invisible.
- **Operational finding — the demo site has no mapped lines.** Live Overpass at Sabetha
  (39.9042, -95.7997, 5 km): water 54, buildings 205, trees 2, **powerline 0**. Correct but inert
  there; will not show in the flagship demo. Verified working against real data elsewhere (Topeka
  KS: 29 lines). This is the sparse-rural-OSM failure mode this project already hit with parcel
  boundaries, and why the "absence of a mapped line is not evidence of no line" note is always-on.
- **Next in Lane B:** connector-leg rerouting around keepouts. It just became materially more
  urgent — a transit leg crossing a keepout is still only COUNTED (`keepout_overflights` + amber
  warning), not rerouted, and for a powerline that is a collision rather than a wasted pass.
#### Guardian monitor SITL proof — Part 3C, 2 of 3 monitors (2026-08-18, Lane A)
Ran in an isolated worktree (`.claude/worktrees/lane-b`, branch `lane-a/guardian-proof`) while a
second session worked Lane B (powerline keepouts) in the shared checkout — disjoint file sets, no
conflicts. NOTE: the worktree needed `sitl/ArduPlane.exe` + the cygwin DLLs copied in, because
`sitl/*.exe|*.dll` are gitignored and `routers/sim.py` resolves the binary from
`Path(__file__).parents[3]` — a fresh worktree therefore has no simulator. Ran against the shared
`backend/venv` (no separate venv needed; deps come from the venv, `app` from the worktree cwd).

**Shipped**
- Two new verified-write, restore-on-clear faults in `routers/sim.py`: `gps_noise`
  (`SIM_GPS1_HNSE`) and `airspeed` (`SIM_ARSPD_FAIL`), plus curated `param_meta` ranges for both.
  Restore bookkeeping generalized from a single `_batt_prev` global to a `_prev` dict, with a
  guard so re-injecting an active fault can't capture the FAULTED value as the restore point.
- `test_scenario_ekf_variance.py` + `test_scenario_airspeed_stall.py`, registered in
  `scenarios.ps1` as `ekf-variance` / `airspeed-stall`. Harness gained `guardian()`,
  `set_guardian()` and `wait_warning()` (matches on the operator-facing warning TEXT on purpose —
  if the string stops naming the condition, the scenario fails).
- 270 unit tests green (264 + 6 new).

**The vibration monitor cannot be proven on this SITL, and now has no fault endpoint.**
Nine SIM_* knobs across five flights, every write verified by the M1b echo path, none moved the
reported VIBRATION levels off ~0.17 m/s/s against a 30 m/s/s threshold. `SIM_VIB_MOT_*` models
multicopter motor vibration and a plane frame has no motors in that list. Full evidence in
`SPRAY-FLIGHT-SAFETY.md` Part 3C.

**Two process lessons worth keeping** — both nearly produced a green test that proved nothing:
1. An early probe showed an apparent 4x vibration response and I took it as the fault working. It
   was airframe dynamics after level-off; that same run's clean baseline peaked HIGHER (0.562)
   than any "injected" reading. Only re-testing through the finished endpoint exposed it.
   Sweeping a param and eyeballing a number that moved is not causation.
2. The first cut shipped a `vibration` fault + a scenario with the guardian threshold lowered to
   ~1.3x the noise floor. Both were removed. A fault endpoint that writes no-op params reports
   "fault injected" on an unaffected vehicle — the exact silent lie the verified-write path
   exists to prevent. `test_m4_sim.py::test_there_is_no_vibration_fault` pins the absence.

**Verification: 12/12 SITL scenarios green in 4:29** (10 existing + 2 new) on an idle machine,
plus 270 unit tests. Both new scenarios also pass in isolation, run twice each.

**But the suite is only trustworthy when nothing else is using the machine — measured, and worth
knowing before anyone debugs a phantom regression.** Three earlier full runs, taken while a second
session worked Lane B, failed 3, 4 and 5 scenarios with a DIFFERENT set each time and degraded
350s → 490s → 802s. Every failure was connection-level (`connect failed: 500 no heartbeat from
vehicle`, `[WinError 10061] refused`, `[WinError 10054] forcibly closed`) — never an assertion.
The control run above, on a quiet machine, was clean. Diagnosis: port 5760 is single-occupancy;
`conftest.py` teardown waits on `/api/sim/status` plus a flat 1 s, which never proves TCP 5760 is
actually free; and `sim._running()` treats a listening port as "already running", so a dying SITL
can be handed straight to the next scenario. The durable fix is Lane D item 3 (parameterize
`harness.py`'s hardcoded `tcp:127.0.0.1:5760`) plus a teardown that waits for the port to clear.
Until then: **re-run a red scenario alone before believing it**, and don't run the suite while a
parallel session is active.

#### Doc reconciliation + full re-verification on a new machine (2026-08-18)
- **No code changes.** Session was a state audit: read every project doc, verified claims against
  the repo, re-ran the whole verification suite, and fixed the doc drift that had accumulated.
- **Re-verified green on the current machine** (profile `C:\Users\jacks`, Python 3.13.12,
  Node v24.15.0): **264 backend unit tests** (12.5s), **10/10 live SITL scenarios**
  (`scenarios.ps1 all`, 4m05s — battery-fault, bench, field-test, gps-failure, guardian,
  link-loss, link-watchdog, preflight, rtl-recovery, soak), **6 frontend tests** (Vitest).
  Environment was already fully installed here (venv, both node_modules, sitl binaries);
  `backend/dist/AgOpsGCS.exe` is not built on this machine.
- **Three stale doc claims corrected:**
  1. **The guardian safety-monitor branch was described as "ready to merge, not yet merged" in
     both the RESUME block and `SPRAY-FLIGHT-SAFETY.md` — it had actually been merged.** `94c4d76`
     and `c401628` sit on `main` on top of `7bb3f60`; the worktree and the
     `worktree-spray-safety-monitors` branch are gone, and `main` is the only branch local and on
     GitHub. The 264-test count is the post-merge number. Corrected in both places, with the real
     remaining gap called out: those three monitors are **unit-tested only** and still have no SITL
     scenario driving them (`SPRAY-FLIGHT-SAFETY.md` Part 3C) — and the port contention that
     blocked that work is gone, so it is now the top of the queue rather than a blocked item.
  2. **`GAP-ANALYSIS.md` M7 still cited `vehicle_manager.py` at 815 lines. It is 1,866** — the
     module more than doubled while the strangler extraction stayed unstarted. Noted there and in
     Still TODO as the largest architectural debt in the repo.
  3. **Windows profile paths were hardcoded to `C:\Users\tabor`** throughout this file (the machine
     has changed at least twice). Made profile-relative, including the billing-table transcript
     method note, which encodes the profile in its `~/.claude/projects/<profile>/` path.
- **Still TODO rewritten and reordered** into a start-now list (SITL scenario proof → live
  keepout-proximity → powerline keepouts → post-flight scorecard → bank-angle/wind → M5 → M7 →
  alert-threshold unification), with the two blocking questions for Caleb promoted to the top:
  the real spray altitude (`CoverageRequest.alt` still defaults to 100m AGL, which reorders every
  low-altitude safety item) and what sensing the pump path actually has.
- **Billing state corrected:** the resume block claimed 2026-08-15 was the only unbilled session.
  Four are unbilled — 2026-08-15 (hardening), 2026-08-15 (guardian monitors), 2026-08-16
  (docs/merge), 2026-08-18 (this one).

#### Guardian safety-monitor expansion (2026-08-15) — SHIPPED; MERGED to `main` 2026-08-16
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
- **MERGED to `main`** (verified 2026-08-18): the work landed as `94c4d76` + `c401628` directly
  on top of `7bb3f60`; the worktree and the `worktree-spray-safety-monitors` branch are both gone,
  and `main` is the only branch local and on GitHub. 264 unit tests green on the current machine.
  **Still open from this work:** the three new monitors have unit tests only — no SITL scenario
  drives them against a live telemetry stream yet (`SPRAY-FLIGHT-SAFETY.md` Part 3C; the port
  contention that blocked it is gone).

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
- Python 3.13 at `~\AppData\Local\Programs\Python\Python313` (3.13.12 on the 2026-08-18 machine; `backend\venv` is the interpreter the exe is built against)

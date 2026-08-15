# Spray-phase flight safety + data tracking — gap analysis

## Status (2026-08-15)
Items 1–3 below (EKF variance, vibration, airspeed/stall-margin monitors) plus Part 3A (flight
log widening) are **DONE** — implemented, unit-tested, and committed on branch
`worktree-spray-safety-monitors` (commits `94c4d76`, `c401628`), built in an isolated git
worktree while another session owned `main`'s working tree. The other session's "Backend
hardening" pass landed on `main` as `7bb3f60` (also touched `vehicle_manager.py`, but in
different functions — `_request_data_streams`, `_guardian_tick`'s landing-RTL suppression,
`snapshot()`'s NaN scrubbing — none of which overlap the TelemetryData fields / message-parsing /
`_maybe_log` regions this work touches). This branch has already been **rebased cleanly onto
`7bb3f60`** (no conflicts) and **264 backend tests pass** — it's ready for a fast-forward merge
into `main` whenever that's wanted; nobody has to resolve anything by hand.

Everything below item 3, and all of Part 3B/3C, is still **not implemented** — this file remains
a hand-off spec for that remaining work.

Originally written 2026-08-14 from a read-only pass over the current backend (`guardian.py`,
`preflight.py`, `vehicle_manager.py`, `eventlog.py`, `backend/tests/sitl/`) while another session
actively owned the repo (uncommitted changes in `vehicle_manager.py`, both coverage routers,
`mission.py`, `vehicle.py`, dev servers up on :8000/:3000, SITL bound to the default port 5760 —
which is also why the new monitors are unit-tested only, not yet proven against a live SITL run;
that needs its own SITL instance once the shared one is free).

## Open question that changes everything below (resolve before prioritizing)
`CoverageRequest.alt` defaults to **100 m AGL**. Real ag-spray passes fly much lower (single-digit
to low-double-digit meters) for coverage/drift reasons — 100 m reads like a value chosen for early
flight-safety validation (more margin for GPS/autopilot error while nothing below has been proven
yet), not a real spray altitude. That's a reasonable place to have started, but it matters a lot
here: at 100 m AGL over flat Kansas farmland, terrain/obstacle risk is close to zero and the
existing guardian monitors already cover most of what can actually kill the flight. At real spray
altitude, bank-angle-near-ground, stall margin, and terrain/AGL clearance stop being theoretical.
**Ask Caleb/the user what altitude an actual spray pass is meant to fly at before treating the
"low-altitude" items below as urgent** — if 100 m is closer to the real number for a while yet
(camera/terrain-avoidance is explicitly a later phase per the TODO list), reorder priority toward
the data-tracking items, which matter at any altitude.

## Part 1 — what's already there (don't rebuild it)
- **`preflight.py`**: one-time go/no-go gate before arm/takeoff (link READY, GPS 3D fix, EKF
  healthy, home known as blockers; battery/RC/fence/sensors as advisories). Runs once, not during
  flight — irrelevant to an in-progress spray pass.
- **`guardian.py`**: the in-flight monitor set. As of this session: link level, GPS fix/sat-count,
  battery voltage (debounced), RTL energy margin, **EKF health + variance (DONE)**,
  **vibration + accelerometer clipping (DONE)**, **airspeed/stall margin (DONE)**. Still nothing
  about bank angle, wind, or proximity to keepouts — items 4–6 below.
- **`vehicle_manager.py` telemetry parsing** subscribes to `GLOBAL_POSITION_INT`, `VFR_HUD`,
  `ATTITUDE`, `SYS_STATUS`, `BATTERY_STATUS`, `EKF_STATUS_REPORT` (now flags + variance),
  `GPS_RAW_INT`, `RC_CHANNELS`, `SERVO_OUTPUT_RAW`, `MISSION_CURRENT`, `NAV_CONTROLLER_OUTPUT`,
  `HOME_POSITION`, and now `VIBRATION`. Roll/pitch/yaw already flow in from `ATTITUDE` for
  whenever the bank-angle monitor (item 4) gets built.
- **Flight log** (`_maybe_log` in `vehicle_manager.py`): one JSONL file per flight, sampled at
  **4 Hz**. As of this session it carries `t, lat, lon, alt, hdg, as, gs, pitch, roll, yaw, volt,
  batt, mode, gps_fix, sats, ekf_flags, ekf_pos_var, ekf_vel_var, vibe, clip, sensor_errors,
  wp_seq, wp_dist` — widened from the original position/attitude/battery/mode-only set (Part 3A,
  DONE). Still missing: guardian monitor state at the same timestamp (Part 3B still pending).
- **`eventlog.py`**: structured event log already records sensor-health transitions, guardian
  triggers, link-state changes, etc.
- **M4 SITL scenario harness** (`backend/tests/sitl/`): `test_scenario_{link_loss, gps_failure,
  battery_fault, rtl_recovery, link_watchdog, guardian, preflight, field_test, bench, soak}.py`.
  **Still no scenario exercises anything spray-pass-specific or proves the three new monitors
  against a real telemetry stream** — Part 3C, still pending, blocked on the shared SITL port.

## Part 2 — in-flight monitor gaps
1. ~~EKF variance~~ **DONE.**
2. ~~Vibration~~ **DONE.**
3. ~~Stall/airspeed margin~~ **DONE** (kept operator-configured rather than reading
   `ARSPD_FBW_MIN`/`AIRSPEED_MIN` from the param cache — guardian.py deliberately stays
   param-cache-free; see the comment in `GuardianConfig`).
4. **Bank/pitch angle during turns near the ground.** `telemetry.roll`/`pitch` already populated.
   A stall-spin with little altitude to recover is unrecoverable; the mitigation isn't "detect
   after the fact" (too late) but partly a *planning-time* constraint in `coverage.py`'s turn
   geometry, and partly a guardian sanity check flagging bank steeper than the plan should ever
   command (catches wind gusts / control saturation). Not started.
5. **Live keepout proximity, not just planning-time avoidance.** `coverage.py`/`coverage_multi.py`
   clip the *planned* path around keepouts, but nothing cross-checks the *actual flown position*
   against those rings in real time. `gis_zones.py` already has the primitive needed —
   `dist_to_zone_m(pt, zone)` — cache the mission's keepout rings + buffer at upload time, run it
   against live position each guardian tick. Lands naturally alongside `POWERLINE-KEEPOUTS.md`,
   since it reuses the same primitive and keepout data both features need. Not started.
6. **Wind (`WIND` message — not currently subscribed).** Spray-drift context for the post-flight
   debrief, and a control-margin proxy against item 3's airspeed monitor. Confirm ArduPilot's
   telemetry stream actually includes `WIND` in the requested `MAV_DATA_STREAM` set before
   assuming it's free. Not started.
7. **Pump/spray-system verification during flight.** Hardware-gated — check with Caleb what
   sensing the pump path actually has (flow/pressure sensor vs. PWM-only) before designing
   further. Not started.
8. **Terrain/AGL clearance.** Explicitly deferred (needs camera + companion computer per the
   project TODO). The "Kansas is flat" assumption is implicitly relied on by every monitor here —
   worth making that an explicit documented assumption somewhere central (e.g. `guardian.py`'s
   module docstring) rather than leaving it implicit. Not started.

## Part 3 — data tracking
**A. Flight log widened.** **DONE** — see Part 1.

**B. No computed "how close did we come" summary yet — only raw replay.** `routers/logs.py`
serves raw samples for `LogsPanel` playback. Missing: a post-flight scorecard generated once on
disarm (mirrors how `_close_log` already runs at that moment) — minimum distance achieved to any
keepout ring, minimum RTL energy margin observed, max bank angle, any EKF variance spikes even if
they stayed under the warn threshold, guardian warning count by type, vibration clip count. This
is what turns "did it crash" into a trend you can watch degrade before it becomes a crash. Not
started; touches `routers/logs.py`, which was in the other session's modified-files set as of
2026-08-14, so check that file's current shape before starting.

**C. Verification: new SITL scenarios, same pattern as M4.** Every monitor above needs its own
`test_scenario_*.py` proving it fires under the exact fault it's meant to catch — mirroring
`link_loss`/`gps_failure`/`battery_fault`. Blocked right now on the shared SITL port (5760); the
existing harness (`backend/tests/sitl/harness.py`) hardcodes `tcp:127.0.0.1:5760`, which the other
session's live instance holds. Do this once that's free, or by running a second SITL instance on
non-default ports if that becomes worth the setup effort. Not started.

## Rollout order (updated)
1. Resolve the altitude question — still unresolved, still reorders priority.
2. ~~EKF variance, vibration, flight-log widening~~ **DONE.** ~~Airspeed/stall margin~~ **DONE.**
3. Live keepout-proximity monitor (#5) — pairs naturally with `POWERLINE-KEEPOUTS.md`.
4. Bank-angle monitor (#4), wind (#6).
5. Post-flight scorecard (Part 3B).
6. SITL scenario proof (Part 3C) — as soon as the shared port is free.
7. Pump verification and terrain/AGL — hardware-gated, need Caleb's input first.
8. ~~Merge `worktree-spray-safety-monitors` into `main`~~ — branch is rebased on `main` and
   green, ready to merge whenever wanted; not yet done as of this writing (ask before merging —
   it's the first thing this branch does that touches shared state).

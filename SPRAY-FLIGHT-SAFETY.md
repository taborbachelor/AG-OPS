# Spray-phase flight safety + data tracking — gap analysis

## Status (2026-08-18, Lane A) — EVERYTHING IN THIS DOC IS NOW DONE OR EXPLICITLY BLOCKED

| Item | State |
|---|---|
| 1-3. EKF variance / vibration / airspeed-stall monitors | Shipped earlier; **now SITL-proven** (2 of 3 — see Part 3C) |
| 4. Bank angle | **DONE** — `bank` monitor + `bank-angle` scenario |
| 5. Live keepout proximity | **DONE** — `keepout_watch.py` + `keepout` monitor + `keepout-prox` scenario |
| 6. Wind | **DONE** — `WIND`/`WIND_COV` parsed; confirmed streamed, no extra subscription |
| 7. Pump/spray verification | **BLOCKED** — pump sensing undecided (asked 2026-08-18) |
| 8. Terrain/AGL clearance | **BLOCKED** — needs camera + companion computer |
| 3A. Flight log widening | Shipped earlier; now also carries wind |
| 3B. Post-flight scorecard | **DONE** |
| 3C. SITL scenario proof | **DONE for what can be proven** — vibration is unprovable on this SITL (see Part 3C) |

Verification: **366 backend unit tests + 14 SITL scenarios + 13 frontend tests.**
The two remaining open items are both hardware-gated. The highest-value software follow-up is no
longer in this doc — it is the **planning-time turn-geometry constraint in `coverage.py`** (the
other half of item 4), which the bank-angle measurements below made a lot more urgent.

## Earlier status (kept for provenance — updated 2026-08-18)
Items 1–3 below (EKF variance, vibration, airspeed/stall-margin monitors) plus Part 3A (flight
log widening) are **DONE AND MERGED TO `main`** — commits `94c4d76` and `c401628`, which sit
directly on top of the other session's "Backend hardening" pass (`7bb3f60`). They were built in
an isolated git worktree while that session owned `main`'s working tree, rebased cleanly (the two
sessions touched `vehicle_manager.py` in disjoint functions), and merged on 2026-08-16. The
worktree and the `worktree-spray-safety-monitors` branch no longer exist; `main` is the only
branch local and on GitHub. **264 backend tests pass** — re-verified 2026-08-18.

**The important caveat on that "DONE":** those three monitors have **unit tests only**. Nothing
drives them against a real telemetry stream. That was blocked on SITL port 5760 being held by a
concurrent dev instance — **that contention is gone**, so Part 3C (scenario proof) is the honest
completion of items 1–3, not optional polish.

Everything below item 3, and all of Part 3B/3C, is still **not implemented** — this file remains
a hand-off spec for that remaining work.

Originally written 2026-08-14 from a read-only pass over the backend (`guardian.py`,
`preflight.py`, `vehicle_manager.py`, `eventlog.py`, `backend/tests/sitl/`) while another session
actively owned the repo.

## ~~Open question~~ ANSWERED 2026-08-18: a real spray pass flies **10–25 m AGL**
Confirmed by the user. `CoverageRequest.alt` still defaults to 100 m, so the default is now known
to be ~4-10x the real operating altitude — that default is a live decision to revisit, not a
setting anyone has validated for spray work.

**What that answer reorders:** the low-altitude items below stop being theoretical. At 10–25 m
the aircraft is inside the wire-strike envelope (rural distribution lines run ~8–12 m, transmission
~20–45 m), so powerline keepouts and the connector-leg rerouting that goes with them are airframe
survival, not spray quality. Bank-angle (#4) and terrain/AGL (#8) move up for the same reason: a
stall-spin at 15 m has no recovery altitude. The data-tracking items keep their value at any
altitude.

The original framing is kept below for provenance.

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

## Connector-leg rerouting — SHIPPED 2026-08-18
Related to item 5 below (live keepout proximity) but distinct: this is
PLANNING-time avoidance, not an in-flight monitor. Connecting legs — the hops
between spray sub-segments, the inter-field transits, and the home leg — used
to fly straight through keepouts and were only COUNTED. That was fine while
every keepout protected spray quality; powerline keepouts made it a collision
risk. See `app/reroute.py` and the "Connector-leg rerouting" History entry in
`CLAUDE-CALEB.md` for the full design, including why the hazard set is a
SUBSET of keepouts (rerouting around every treeline costs flight time for no
safety gain) and why the home leg is reported rather than routed (RTL is
autopilot-controlled and flies straight).

Item 5 below is still open and still worth doing: planning-time avoidance does
not help if the aircraft drifts off the planned path, so the in-flight
proximity monitor remains the second layer.

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
  against a real telemetry stream** — Part 3C, still pending, no longer blocked (port is free).

## Part 2 — in-flight monitor gaps
1. ~~EKF variance~~ **DONE.**
2. ~~Vibration~~ **DONE.**
3. ~~Stall/airspeed margin~~ **DONE** (kept operator-configured rather than reading
   `ARSPD_FBW_MIN`/`AIRSPEED_MIN` from the param cache — guardian.py deliberately stays
   param-cache-free; see the comment in `GuardianConfig`).
4. ~~**Bank/pitch angle during turns near the ground.**~~ **DONE 2026-08-18** — guardian `bank`
   monitor + `test_scenario_bank_angle.py` (`.\scenarios.ps1 bank-angle`). Default threshold is
   45 deg, pinned to ArduPlane's own `ROLL_LIMIT_DEG` default rather than picked by feel: past it
   the aircraft is banking harder than the autopilot should ever command, which is exactly the
   gust / saturation / stick-input case this catches. The limit tightens automatically below
   `bank_low_alt_m` (30 m -> x0.7), because that is where a stall-spin has no recovery room.
   Warn-only by default; `bank_action="rtl"` available with a 2 s debounce so a gust can't trip it.

   **MEASURED FINDING, and the real story here: this airframe banks 50-65 deg during ORDINARY
   loiter and RTL turns in SITL — past ROLL_LIMIT_DEG.** So the monitor fires on routine turns
   today. That is a true finding about the aircraft, not a mis-set threshold: a 60 deg bank raises
   stall speed ~41%, and at the confirmed 10-25 m spray altitude there is no altitude to recover a
   stall-spin. **This makes the planning-time turn-geometry constraint (the other half of this
   item, in `coverage.py`) materially more urgent than it looked** — the monitor only makes the
   problem visible; it cannot make the turns gentler.
5. ~~**Live keepout proximity, not just planning-time avoidance.**~~ **DONE 2026-08-18** —
   `app/keepout_watch.py` + guardian `keepout` monitor + `test_scenario_keepout_proximity.py`
   (`.\scenarios.ps1 keepout-prox`). Rings are supplied by the client via
   `POST /api/safety/keepouts` (the coverage response's `zones` shape is accepted directly) and
   checked against live position every guardian tick using `gis_zones.dist_to_zone_m` — imported,
   never reimplemented, because two copies of point-in-polygon that disagree is how you get a wire
   strike. Design points worth keeping:
   - Only **hazard** kinds (powerlines) warn. Water/trees/buildings distance is measured and
     reported for the debrief but never annunciated — overflying a pond with the sprayer off costs
     nothing, and warning every pass would train the operator to ignore the annunciator. Same
     hazard/keepout split `reroute.py` uses.
   - `known` is reported separately from `ok`: **no zone data must never render as a green tick.**
   - **Mission upload CLEARS the cached rings.** Rings from the previous field are worse than none
     — they read as a confident all-clear over ground nobody surveyed. The client must re-arm the
     monitor after every upload.
   - Warn-only by default on purpose: an RTL turns the aircraft toward home, which could steer it
     ACROSS the very line it is close to. The operator decides.
6. ~~**Wind (`WIND` message — not currently subscribed).**~~ **DONE 2026-08-18.** The doc asked to
   confirm ArduPilot actually streams it before assuming it was free — **it does**: verified live
   with `SIM_WIND_SPD=12 / SIM_WIND_DIR=270`, `telemetry.wind_speed` tracked it 0.1 -> 12.0 m/s at
   bearing 270 under the existing `MAV_DATA_STREAM_ALL` request, no extra subscription needed.
   Both `WIND` (speed/direction) and `WIND_COV` (NED components, converted) are parsed to the same
   two fields. Carried in the flight log and the scorecard: without it, a low-airspeed or
   high-bank sample in the log can't be told apart from a gust.
7. **Pump/spray-system verification during flight.** Hardware-gated. Asked 2026-08-18: the pump
   sensing is **not decided yet**, so this stays out of scope rather than being designed against a
   guess. Re-ask before starting. The answer is load-bearing: with a flow or pressure sensor,
   real spray verification and a no-flow abort are buildable; PWM-only means verification can
   never be more than inferential (commanded state + flight log), and building something that
   *looks* like confirmation on PWM-only would be worse than leaving it out. Not started.
8. **Terrain/AGL clearance.** Explicitly deferred (needs camera + companion computer per the
   project TODO). The "Kansas is flat" assumption is implicitly relied on by every monitor here —
   worth making that an explicit documented assumption somewhere central (e.g. `guardian.py`'s
   module docstring) rather than leaving it implicit. Not started.

## Part 3 — data tracking
**A. Flight log widened.** **DONE** — see Part 1.

**B. Post-flight scorecard.** **DONE 2026-08-18.** Generated once on disarm, in `_close_log`
(the only moment "the flight" is a finished thing that can be summarised), written as
`flight_<stamp>.scorecard.json` beside the log and served on `GET /api/logs/{name}` as
`scorecard` (the list view carries `has_scorecard`). Also emitted to the event log.

Carries: min distance to any hazard ring and to any keepout ring, min RTL energy margin, max bank
angle, max EKF pos/vel variance, max vibration + clip events, max wind, min airspeed, min battery
voltage, flight duration, and **guardian warning counts per monitor**.

Two decisions worth keeping:
- **Every extreme starts as `None`, never 0.** "No wind data this flight" and "zero wind this
  flight" are different facts, and a scorecard reporting `0 m` to the nearest powerline when no
  rings were ever loaded would be a dangerous lie.
- **Warnings are counted per EPISODE (rising edge), not per tick.** A tick count on a 1 Hz sampler
  reports how *long* a condition held while looking like how *often* it happened.

The point is the near miss: an EKF variance that peaked at 0.55 against a 0.6 threshold never
warns and leaves no trace in the event log — but three flights of that in a row is a degradation
trend you want to see before it becomes an incident. A scorecard is absent (`null`) for flights
recorded before this existed and for any flight the backend never saw disarm, so callers must
treat missing as "not available", never as "nothing to report".

**C. Verification: new SITL scenarios, same pattern as M4.** Every monitor above needs its own
`test_scenario_*.py` proving it fires under the exact fault it's meant to catch — mirroring
`link_loss`/`gps_failure`/`battery_fault`.

### 2026-08-18 — PARTIALLY DONE. Two of the three monitors are now proven; the third cannot be.

Two new scenarios, two new fault types in `routers/sim.py` (both verified-write, both
restore-on-clear, same shape as the battery fault):

| Monitor | Scenario | Fault | Result |
|---|---|---|---|
| EKF variance | `test_scenario_ekf_variance.py` (`.\scenarios.ps1 ekf-variance`) | `gps_noise` → `SIM_GPS1_HNSE` | **PROVEN.** 10 m of GPS noise drives `pos_horiz_variance` to ~2.5 against the 0.6 threshold, with `ekf_healthy` still True — the drifting-but-not-yet-unhealthy branch the monitor exists for. |
| Airspeed / stall margin | `test_scenario_airspeed_stall.py` (`.\scenarios.ps1 airspeed-stall`) | `airspeed` → `SIM_ARSPD_FAIL` | **PROVEN.** A pitot pinned to 4 m/s raises `airspeed low (… ) — stall risk` within ~1 s. |
| Vibration / accel clipping | *none — see below* | *none* | **NOT PROVABLE on this SITL. Still unit-tested only.** |

**The vibration monitor cannot be driven from this SITL build, and that is now a measured fact
rather than an assumption.** Tried in flight at 80 m across five flights: `SIM_VIB_MOT_MAX`,
`SIM_VIB_MOT_MULT`, `SIM_VIB_MOT_MASK`, `SIM_VIB_MOT_HMNC`, `SIM_VIB_FREQ_X/Y/Z`,
`SIM_ACC1_RND`, `SIM_ACCEL1_FAIL`, `SIM_ACC1_SCAL_*`. Every one was verified as actually written
to the vehicle (M1b echo path) and **not one moved the reported VIBRATION levels** — steady
flight sits at ~0.17 m/s/s with those params at their extremes exactly as it does with them at
zero, against a 30 m/s/s threshold. Accelerometer clipping never occurs at all.

Two traps worth recording, because both nearly produced a green test that proved nothing:
- An early probe appeared to show a 4x response to `SIM_VIB_MOT_MASK=1`. It was **airframe
  dynamics after takeoff level-off, not the fault** — the same run's clean baseline peaked at
  0.562, *higher* than any "injected" reading. Only re-testing through the fault endpoint caught
  it. Correlation, not causation.
- The first version of this work shipped a `vibration` fault type and a scenario that lowered the
  guardian threshold to ~1.3x the measured noise floor. Both were **removed**: a fault endpoint
  that writes params which change nothing would report "fault injected" on a vehicle that is
  unaffected — the exact silent lie the verified-write path exists to prevent.

`SIM_VIB_MOT_*` appears to model *multicopter* motor vibration; a fixed-wing frame has no motors
in that list. If the vibration monitor needs live proof, the options are a quadplane/copter SITL
frame, a replay-based test driving recorded VIBRATION messages into the parser, or real bench
hardware. `test_m4_sim.py::test_there_is_no_vibration_fault` pins the absence so nobody re-adds
the endpoint without also adding a scenario that works.

## Rollout order — COMPLETE except the hardware-gated items
1. ~~Resolve the altitude question~~ **ANSWERED: 10-25 m AGL.**
2. ~~EKF variance, vibration, flight-log widening, airspeed/stall margin~~ **DONE.**
3. ~~Live keepout-proximity monitor (#5)~~ **DONE.**
4. ~~Bank-angle monitor (#4), wind (#6)~~ **DONE.**
5. ~~Post-flight scorecard (Part 3B)~~ **DONE.**
6. ~~SITL scenario proof (Part 3C)~~ **DONE** for the two monitors that can be driven; vibration
   is unprovable on this SITL build and is recorded as such rather than faked.
7. Pump verification and terrain/AGL — hardware-gated, still need Caleb's input.
8. ~~Merge `worktree-spray-safety-monitors` into `main`~~ **DONE 2026-08-16** (`94c4d76`,
   `c401628`); branch and worktree deleted.

## What this doc no longer covers — the next thing worth doing
The bank-angle work turned up the one finding that should drive the next piece of software:
**the aircraft banks 50-65 deg in ordinary autopilot turns, and a real spray pass flies at
10-25 m.** In-flight detection is now in place, but detection cannot make a turn gentler. The
mitigation is the **planning-time turn-geometry constraint in `coverage.py`** — cap the commanded
bank the serpentine turn geometry can produce at spray altitude. That lives in the planner
(Lane B's file set), not here, and it is the natural successor to this document.

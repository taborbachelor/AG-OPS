# M7 — splitting `vehicle_manager.py` along the air/ground line

**Status: planned, not started. No code has moved.** Written 2026-08-20 (delta,
TASK-015) while the file was held by nobody and two other agents were live in
AIR. The cut itself is a solo job and is blocked until AIR is clear — see
*Preconditions*.

---

## What this is not

It is not "the file is big, tidy it up". A refactor with that goal picks
boundaries by what looks neat, and the boundary you get is the wrong one.

The decisions log already fixed the boundary: **guardian belongs onboard,
eventually.** Every monitor runs on the laptop today and goes silent exactly when
the link drops — which is the moment it is most needed, on an airframe whose
whole premise is that it survives and obeys with zero link. `guardian.py` is
already pure logic with full unit coverage, so it ports to a companion computer
without a rewrite. What does *not* port is the 2,547-line file it is currently
welded into.

So the question this refactor answers is one question, asked of every method:

> **When the radio link to the laptop dies, does this still need to run?**

That is the cut. Everything else is filing.

---

## The file as it stands

`backend/app/vehicle_manager.py` — 2,547 lines, `VehicleManager` is 76 methods
and 2,194 of them. Measured, not estimated (`ast`, 2026-08-20 @ `cd17cfd`):

| Bucket | Lines | Share | Side |
|---|---:|---:|---|
| missions / transfers (mission, fence, rally) | 440 | 20.1% | ground |
| link + transport | 364 | 16.6% | **both** |
| parameters | 362 | 16.5% | ground |
| telemetry / state (`_handle_msg`, `snapshot`) | 261 | 11.9% | **both** |
| commands (arm, mode, takeoff, land, RC) | 244 | 11.1% | ground |
| **guardian runner** | 226 | 10.3% | **air** |
| `__init__` | 125 | 5.7% | — |
| **terrain serving** | 74 | 3.4% | **air** |
| flight logging | 69 | 3.1% | either |
| **keepout watch** | 29 | 1.3% | **air** |

**Air-side subtotal: 329 lines, 15%.** That is the payload this refactor exists
to be able to lift out. It is a small fraction of the file and it is tangled
through all of it, which is exactly why the split is worth doing and why it has
not happened by accident.

Twelve modules import `vehicle_manager`; 8 of 10 routers do.

---

## Two things must happen before any code moves

Skip either and the split becomes a rewrite.

### 1. `_handle_msg` is the seam, and it has to stop being an if/elif

205 lines — the single largest method, 9% of the class — dispatching one
MAVLink message type after another, writing directly into telemetry, the
guardian, the param cache and the terrain service. Every layer reaches into it,
so it cannot be assigned to any of them.

**Turn it into a registry before splitting anything:** each layer registers the
message types it owns (`link` takes HEARTBEAT, `parameters` takes PARAM_VALUE,
`air/terrain` takes TERRAIN_REQUEST and TERRAIN_REPORT, and so on), and
`_handle_msg` becomes a lookup and a call. Done first, as its own commit, on the
current structure, the split afterwards is mostly moving whole methods. Done
second or not at all, every layer keeps a private wire into the dispatcher and
nothing is actually separable.

This is the one genuinely structural change in M7. The rest is relocation.

### 2. Six private reads from routers have to become public first

A split renames or relocates these and the callers fail silently or at import:

| Leak | Read by |
|---|---|
| `_link_state`, `_sysid`, `_vehicle_sysid`, `_capabilities` | `routers/connection.py:207-210` |
| `_param_sync` | `routers/vehicle.py:169, 185` |

`snapshot()` already publishes all five. Give them accessors (or route the
callers through `snapshot()`), as a separate commit with its own green run, so
that when the split lands nothing is depending on a private name.

Full external surface: **48 names** are referenced as `vehicle_manager.<name>`
outside the module. The split must keep every one of them resolving, or update
all call sites in the same commit. `connected` (28 uses) and `snapshot` (16) are
the two that matter most.

---

## Target shape

Mapped onto ARCHITECTURE.md's layer list, keeping its names:

```
backend/app/vehicle/
  link.py          transport: connect/disconnect, watchdog, reconnect,
                   heartbeat, sysid filter, recv/send primitives, dispatch registry
  state.py         TelemetryData, message->state decode, snapshot()
  commands.py      mode, arm/disarm, takeoff, land, run_command, RC override
  parameters.py    cache, get/set, atomic set + rollback, sync
  transfers.py     mission / fence / rally upload+download
  flightlog.py     _maybe_log, _open_log, _close_log

backend/app/onboard/          <-- the point of the exercise
  guardian_runner.py          the loop + tick + flight stats + scorecard
  keepouts.py                 proximity monitor arming
  terrain.py                  TERRAIN_REQUEST/REPORT handling
```

`onboard/` is the half that must keep running with no laptop. Nothing in it may
import from a router, from FastAPI, or from anything that assumes a GCS is
listening — and that rule is worth a test, because it is the property the whole
refactor buys and it is easy to lose one import at a time.

`vehicle_manager.py` stays as a thin façade re-exporting the 48-name surface, so
routers and tests are untouched by the move itself. Collapsing the façade is a
later, separate decision.

---

## Order of operations

Strangler, not big-bang. Each step is its own commit with the full suite green.

1. Dispatch registry (prerequisite 1). No files move.
2. Public accessors for the six leaks (prerequisite 2). No files move.
3. `onboard/terrain.py` — smallest air-side piece, newest code, best-covered.
4. `onboard/keepouts.py` — 29 lines, mechanical.
5. `onboard/guardian_runner.py` — the real one. 226 lines, and the reason for all of this.
6. `vehicle/parameters.py`, then `vehicle/transfers.py`, then `vehicle/commands.py`.
7. `vehicle/link.py` + `vehicle/state.py` last: everything else has moved off them by then.

Stop after any step. A half-done split in this order is still a working system,
which is the property that makes it safe to hand off.

---

## Invariants a refactor will break if nobody is looking

These are load-bearing and mostly documented only as comments inside the methods
that implement them. Each one cost real time to find. Moving the code moves the
comment; it does not move the reason.

- **`_recv_blocking` must keep bumping the watchdog and sending our heartbeat.**
  A plain `recv_match(type=...)` silently discards every other message including
  HEARTBEAT, so any long transaction starves the link watchdog and tears down a
  healthy link the moment the lock releases.
- **RX sysid filtering in both receive paths.** A co-connected Mission Planner
  must not feed our watchdog or answer our transactions.
- **Lock discipline:** `_telem_lock` around multi-field telemetry writes so
  `snapshot()` never sees a torn update; `_send_lock` around every send;
  `_link_lock` around whole transactions. Splitting into modules multiplies the
  chances of taking these in a different order — decide the order once and write
  it down.
- **MAVLink 2 dialect is selected at import, before any module-level constant.**
  Move that line and fence/rally transfers silently become regular missions that
  overwrite the flight plan while reporting success.
- **Mission transfer answers the exact `seq` the vehicle asked for**, never a
  local counter, and cancels a half-open transaction on failure.
- **`_json_sanitize` at the single choke point** — NaN/inf anywhere in a
  snapshot is invalid JSON for every WS client.
- **Sentinels that mean "unknown", not a value:** `home_position()` returns
  `None` rather than 0,0 (a real place in the Atlantic); `terrain_spacing == 0`
  means no data, not sea level. Both are one careless `or 0` from being lost.

## Verification

14 test files touch `VehicleManager`; the suite is 531 unit tests plus 17 SITL
scenarios. Unit green is necessary and **not sufficient** — this file is where
locking, threading and link lifecycle live, and none of that is fully covered by
unit tests. Any step that touches `link.py` or `state.py` needs a SITL run
holding `sitl-5760` before it is called done.

## Preconditions

- **Solo.** The task text says so and LANES says so: 8 of 10 routers import this
  file, so it collides with everything.
- **Blocked as written (2026-08-20):** alpha holds TASK-011, whose SITL terrain
  proof runs straight through `_handle_msg` and `_serve_terrain_request`; bravo
  holds TASK-006 in the same area. Cutting now moves code out from under a live
  run.
- Steps 1 and 2 are narrow enough to be worth doing the moment the file is free,
  even if the rest waits.

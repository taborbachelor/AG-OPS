# LANES — parallel session coordination (SUPERSEDED 2026-08-19)

> ## ⛔ Coordination has moved to **AgOps**. Start at `.agops/RUNBOOK.md`.
>
> `tools/claim.py`, `.claim/` and the lane/area model described below are no longer wired to
> anything. `.claude/settings.json` now calls the AgOps hooks, and coordination state lives in
> `.agops/`. Everything this file did — ownership, the SITL lock, staleness — AgOps does, plus a
> shared task queue, agent-to-agent messaging, dependencies and a live board.
>
> **This file is still worth reading for two things, and they are the two no tool can infer:**
> the **seam register** and the **append-only decisions log**. Both remain authoritative. The
> "Field notes for a redesign" section at the bottom is what AgOps was built from.
>
> | Old | New |
> |---|---|
> | `py tools\claim.py claim --area AIR` | `py tools\agops.py assign TASK-0XX <agent>` |
> | `py tools\claim.py status -v` | `py tools\agops.py monitor` (or `tools\monitor.cmd` live) |
> | `py tools\claim.py take --resource sitl-5760` | `py tools\agops.py take sitl-5760` |
> | editing your block in this file | the board updates itself |

**Historical, from here down.** Any number of Claude sessions can work this repo at once without
overlapping; that guarantee used to be enforced by a claim registry and a PreToolUse hook.

- **Machine truth (was):** `tools/claim.py` + `.claim/` (gitignored runtime state).
- **Human truth:** this file — the seam register and the decisions log, which no tool can infer.

```
py tools\claim.py areas                # what work areas exist
py tools\claim.py status -v            # who holds what, what's free
py tools\claim.py claim --session <your session_id> --area AIR --label "onboard fences"
py tools\claim.py release --session <your session_id>
```

### Finding your own `session_id`

**It must be the real one.** The guard checks the `session_id` in its hook payload, so a claim filed
under any other string leaves you blocked from your own files. Do not invent one.

- **Sessions started after this was installed:** the SessionStart hook tells you outright — look for
  *"YOUR session_id is: …"* in your opening context.
- **Sessions already running:** run the handshake, which catches the id where it actually lives —
  inside the hook:
  ```
  echo CLAIM_WHOAMI:alpha && cat .claim/whoami-alpha.txt
  ```
  Swap `alpha` for anything unique per session (`bravo`, `charlie`) so two handshakes can't collide.
  The file contains your real id; use it verbatim as `--session`.

---

## How the guarantee actually works

1. **Claim before you edit.** Areas are file-disjoint by construction (`AIR`, `PLANNER`, `UI`,
   `OPS`, `DOCS`). Claiming expands globs against the real tracked file list and intersects them
   against every live claim, so an overlap is refused **before** you start typing, and it names the
   contested files.
2. **A PreToolUse hook blocks the edit** if the target belongs to another live session. Wired in
   `.claude/settings.json` (project-level, git-tracked, so every session and worktree inherits it).
   Blocks `Edit`/`Write`/`NotebookEdit`, and mutating `Bash`/`PowerShell` — read-only greps and cats
   across lanes stay allowed, because reading another lane's code is normal and useful.
3. **Solo is free.** With fewer than two sessions registered the guard is inert. Friction appears
   only when a second session shows up, so single-session work is unchanged.
4. **Claims expire on their own.** Every claim carries a 90-minute heartbeat that the hook renews as
   you work. A session that dies goes stale and frees its area — no manual cleanup, no stuck lane.
5. **Exclusive resources** use the same registry: `sitl-5760` (the SITL port is single-occupancy),
   `serial-fc`, `exe-build`, `git-push`.
   ```
   py tools\claim.py take --session <id> --resource sitl-5760      # before scenarios.ps1
   py tools\claim.py drop --session <id> --resource sitl-5760      # the moment you're done
   ```

### Overlap only happens when Tabor says so

Overlap requires an override token that only Tabor sets:

```
py tools\claim.py set-token --token <something-only-you-know>      # Tabor, once
py tools\claim.py grant --session <id> --glob "backend/app/guardian.py" \
       --token <token> --reason "Tabor: B needs the bank threshold constant"
```

A grant is scoped to exactly what it names — granting one file does **not** open the rest of that
area — and every grant, and every refused attempt, lands in `.claim/audit.log`. **Also add a row to
the decisions log below**, because that is the git-tracked record that outlives this machine.

**Honest limit:** this stops accidents between cooperating sessions. It is not a security boundary —
any agent with write access to the repo can edit `claim.py` itself. What it guarantees is that
overlapping work is *deliberate and recorded*, never silent.

---

## Rules the tooling can't enforce

1. **Edit only your own block** in the Live board below. Never reformat or renumber another
   session's section. If an `Edit` fails with *"file modified since read"*, re-read and retry
   immediately — someone just wrote. `LANES.md` is deliberately exempt from the guard so every
   session can update its own block; block discipline is what keeps that safe.
2. **Wire your own caller.** Never ship a backend surface whose only caller lives in another
   session's area. That is exactly the `86c6a6e` cross-lane bug: a complete, tested, green keepout
   monitor that nothing ever armed, because the seam belonged to nobody. Cross-area dependencies go
   in the **seam register** with a named owner.
3. **Seam pass before you close.** Endpoints with no caller. UI reading fields nothing sets.
   Defaults on both sides that must agree.
4. **A red SITL scenario during parallel work is more likely contention than a regression** — hold
   `sitl-5760` and re-run alone before believing it. Three overlapping runs on 2026-08-18 failed a
   *different* set of scenarios each time and degraded 350s → 490s → 802s.
5. **M5 and M7 are solo-only.** `vehicle_manager.py` is 2,057 lines and 8 of 10 routers import it;
   either one collides with everything. They need a session with no other sessions running.
6. **Never junction (`mklink /J`) `node_modules`** or any shared dir from a worktree — deleting the
   worktree follows the junction and wipes the *target*. It cost us the main checkout's
   `node_modules` on 2026-08-18. A worktree needing node deps runs its own `npm install` (~9 s).

---

## Live board

Registry state is authoritative — run `py tools\claim.py status -v`. This board is for the *why*:
what you're actually building, which the registry can't know.

### AIR — onboard enforcement
*Goal: the aircraft survives and obeys with **zero link**.*
- **Session:** `1681cef0` (alpha) — **CLOSED 2026-08-19, all work pushed, claims released.**
- **Shipped:** item 1 (polygon exclusion fences) + the MAVLink 2 migration that item 1 turned out to
  require. `45faa48` `0a928c7` `54ce0d9`. S2 accepted, S3 semantic ruled — see the decisions log.
- **Verification state:** 320 unit tests green, **15 SITL scenarios green in 5:10** (all 14
  pre-existing ones re-proven on MAVLink 2, plus the new `onboard-fence` scenario).

> #### 📋 FOR WHOEVER WRITES `CLAUDE-CALEB.md`'s RESUME HERE (charlie holds DOCS)
> AIR could not edit that file — DOCS was claimed. These are the facts it needs:
>
> 1. **The link is now MAVLink 2.** `vehicle_manager` calls `mavutil.set_dialect()` at import.
>    `mission_type` is a MAVLink 2 message extension; on the old v10 bindings a fence or rally
>    transfer is accepted as a **regular mission** and silently overwrites the flight plan while
>    reporting success at every step. **Do not revert** — rally points need this too.
> 2. **Exclusion fences ship hazard rings to the FC**, built from the same `keepout_watch.prepare()`
>    rings the proximity monitor uses, so hard fence and soft monitor cannot diverge. Only HAZARDS
>    are fenced; the ring goes up raw; overflow refuses rather than truncates; home inside or within
>    30 m of an exclusion is refused. New: `app/onboard_fence.py`, `upload_fence`/`download_fence`/
>    `clear_fence`, `GET /api/safety/exclusions`, and `push_to_vehicle` on `POST /api/safety/keepouts`.
> 3. 🔴 **CORRECTION — `terrain/N39W096.DAT` IS A 0-BYTE PLACEHOLDER.** Both `CLAUDE-CALEB.md` and
>    this file previously described it as terrain data "sitting unused", which reads as *we have the
>    data, just wire it up*. **We do not have it.** There is no terrain data in the repo and no
>    `TERRAIN_*` handling in the backend. Terrain following needs the `TERRAIN_REQUEST`/`TERRAIN_DATA`
>    protocol implemented in the GCS **plus a real SRTM data source** — comparable in size to the
>    fence work, and it needs a bundle-vs-fetch decision from Tabor. Please fix this wording wherever
>    it appears; it is actively misleading about how much work item 2 is.
> 4. **Rally points are now unblocked** by the MAVLink 2 change and are the recommended next AIR
>    item — a link-loss RTL currently flies a straight line home, possibly straight through the
>    powerline corridor we just fenced. Fences stop the aircraft entering; rally points give it
>    somewhere correct to go. That pairing is the whole link-loss story.
> 5. **`AgOpsGCS.exe` still does not exist on this machine** and now also predates the MAVLink 2
>    change. A bench day still has nothing to run.

1. **Polygon exclusion fences uploaded to the FC.** Greenfield, verified 2026-08-19: nothing sets
   `FENCE_TYPE` bit 4 (`FENCE_TYPE_CIRCLE_ALT = 3` is alt+circle only), and keepouts exist *only*
   in the GCS. Upload the plan's keepout polygons over `MISSION_TYPE_FENCE` so they're enforced
   onboard with no link. Consume the payload in **Seam S1**; wire the caller in `routers/mission.py`.
2. **Terrain following.** Bigger than it looks — see the correction above. `terrain/N39W096.DAT`
   is **0 bytes**; there is no terrain data and no `TERRAIN_*` handling. Needs the
   `TERRAIN_REQUEST`/`TERRAIN_DATA` protocol in the GCS plus an SRTM source (Tabor's call:
   bundle tiles or fetch). Still not optional at 10–25 m AGL — just not a small job.
3. **Rally points** — so a link-loss RTL diverts to a safe alternate instead of flying home
   *through* a mapped powerline. Only the `mission_rally` capability bit is decoded today.
4. A SITL scenario proving each of the above with the link deliberately dead.

### PLANNER — coverage, GIS, hazards
*Goal: the plan is flyable.*
- **Session:** `c63d3a97` (bravo) · **Working on:** items 1 and 2 — turn geometry and headlands
  — **BOTH SHIPPED**, see below. PLANNER's queue is now empty.
  **S2 answered with a number and a measurement** (decisions log);
  alpha owns accepting or superseding it. **S3 untouched** — the constraint keys off SPEED, not
  altitude, so it does not need the AGL question settled and I have not pre-empted it.
  ⚠️ **The PreToolUse guard does not load for bravo either**, same cause charlie documented
  (project dir is the home directory, not the repo). I held the PLANNER boundary by discipline;
  every file I touched is inside my claim. My claim also never auto-heartbeats for the same
  reason — I renew it by re-running `claim`, so treat a stale-looking bravo claim as suspect
  rather than dead.

1. ~~**🔥 Turn-geometry bank constraint in `coverage.py`**~~ **DONE 2026-08-19 (bravo).**
   The planner no longer turns onto the adjacent pass. It flies every Nth line and fills the gaps
   on later sweeps, so each reversal gets `2 * R_min` of lateral room at the planned speed
   (`R = V²/(g·tanφ)`). Every line is still flown exactly once — **coverage is bit-identical, only
   the ORDER changes** — and the price is hop distance, measured below. `stats.turn_*` reports the
   geometry actually built, never the geometry requested. On by default (`max_bank_deg=25`),
   `0` restores the old serpentine. 18 new tests; 409 unit tests green.

   **The measurement that justifies it:** the old adjacent-line serpentine demanded **73.2°** of
   bank at 20 m swath / 18 m/s. That is past what the airframe can fly, which is *why* SITL saw
   50–65° — the autopilot was saturating its roll limit, not tracking the plan. Real 40-acre
   Sabetha-shaped field: **73.2° → 25.3°, at +38% path length**. 400×200 m: 73.2° → 33.5°, +19%.
   800×800 m: 73.2° → 22.4° (inside the limit), +26%.

   **Two limits worth knowing, both reported rather than hidden.** (a) A field narrower than
   `2·R_min` cannot satisfy the limit by ordering alone — the widest turn is bounded by the field.
   The planner flies the widest geometry available and reports `turn_bank_ok: false` plus
   `turn_max_speed_ms`, the speed that WOULD meet the limit, because R falls with V² and slowing
   down is the only remaining lever. (b) Detour corners around hazard hulls, and same-heading
   repositions, are not modelled — the pass turnaround is the one that happens hundreds of times
   per job at 15 m AGL.

   **Caught and fixed en route:** `_order_segments_around_hazards` is a nearest-unflown greedy, and
   nearest-unflown *is* the adjacent pass — so it silently undid the whole constraint on any field
   with a hazard on it. Which is the worst possible place to lose it. It now ranks
   spacing-satisfying candidates first and only accepts a tight turn when nothing else is
   reachable. Regression-tested (`test_hazard_ordering_does_not_re_tighten_the_turns`).
2. ~~**Headlands**~~ **DONE 2026-08-19 (bravo).** Measured first, and the measurement moved the
   fix: on a field with a *traced* boundary, 0.93 acres missed — **0.76 along the field edge, only
   0.17 around the keepout.** This is a boundary problem first, not a keepout problem.
   **Mechanism:** a pass sprays a swath-deep BAND but was clipped to where the boundary sits at the
   line through the band's middle, so every row in the band where the field reaches further out was
   missed — a sawtooth strip along any edge not parallel to the passes.
   **Fix:** widen each pass to cover its own band (crossings over `[c-half, c+half]`, not just at
   `c`). The extremes are **exact, not sampled** — between vertices an edge is straight, so the
   widest crossing can only occur at a band edge or a vertex inside the band.
   **Result: 97.6% to 99.6% coverage, 0.93 to 0.17 acres missed, for +3.2% path length.** The
   residual 0.17 is the keepout share, left deliberately (below). 9 new tests; 419 green.

   **Explicitly NOT a perimeter lap.** That is the ground-rig answer and it is wrong for an
   aircraft: a boundary ring's corners are the field's own corners — 90° turns at a point, exactly
   the unflyable geometry item 1 just spent +38% path length removing — plus a whole extra lap of
   flight time. Widening adds **no passes and no turns**; `test_widening_does_not_disturb_the_turn_
   geometry` pins that the two constraints do not fight.

   **The cost is bounded overspray, and the bound is geometric, not lucky:** a widened endpoint sits
   on the boundary crossing of some row within half a swath of its own line, so it can never be more
   than half a swath outside the field. Measured worst case 9.1 m against a 10 m half-swath. This is
   the same overhang the sweep grid already produced in the other axis. `headlands=false` on both
   coverage endpoints for an organic neighbour, a road or a waterway.

   **The keepout share stays missed, on purpose.** Closing it means spraying nearer a pond than the
   clip allows, and that buffer is a chemical-drift guarantee with tests asserting it. Coverage is
   not worth eroding a standoff — it stays visible in `coverage_pct` instead.

### UI — GCS operator frontend
*Goal: effortless.*
- **Session:** — (released 2026-08-19) · **Last held by:** `b06ca0f4` (charlie), which shipped
  items 1–3 and the DOCS close-out. **The lane is FREE and the queue below is empty except the
  new item 4.** Environment findings from that session moved to *Field notes for a redesign*
  rather than living here, because a board note about the environment reads as current fact
  long after it stops being true.

1. ~~**Scorecard UI in `LogsPanel.jsx`.**~~ **DONE 2026-08-19 (charlie).** New
   `components/Scorecard.jsx` rendered from the playback view, a `scorecard` badge on list rows
   that have one, 8 new tests (24 frontend total), both invariants mutation-checked. It holds **no
   thresholds** — the card carries none and M6 keeps threshold judgement in the guardian, so the
   only thing coloured is the guardian's own per-monitor warning counts. Original finding: The backend writes one on every disarm and serves it on `GET /api/logs/{name}`
   (+ `has_scorecard` in the list view), and no operator can see it. Min hazard distance, min RTL
   margin, max bank, warning counts per monitor. Pure consumer of an existing surface — no seam.
2. ~~**One-verdict preflight.**~~ **DONE 2026-08-19 (charlie).** One state word (READY /
   NOT READY / CHECKING), one sentence naming the server's own failing blockers or advisories,
   per-check detail behind a real `<button>` disclosure closed by default, OVERRIDE kept outside
   it because it is an action. 6 new tests (30 frontend total), both invariants mutation-checked.
   **Found and removed an actual M6 regression while doing it:** the panel fell back to two
   locally-invented checks (link + GPS) whenever the server poll had not landed, so it could show
   a PASS the backend would refuse — with `connected` and a GPS fix, the old fallback read READY
   against an unreachable gate. No verdict now renders as CHECKING and never as a pass. The
   client-side `gpsReady` hint went with it, along with three orphaned CSS rules.
3. ~~3D scene eyeball check (needs Tabor's eyes)~~ **RESOLVED BY MEASUREMENT 2026-08-19
   (charlie)** — it did not need eyes. Cesium is a dependency, so the real library was asked
   where the model's nose ends up and checked against the physical requirement. **It was wrong
   in two independent ways.** (a) Heading was **90 deg off**: `headingPitchRollQuaternion`
   resolves the body frame against an EAST-north-up frame, so body +X pointed east at heading 0
   while the model is built nose-along-+X — heading 0 measured a bearing of 90.00. That is the
   sideways aircraft. (b) **Pitch was inverted** — an unrelated bug nobody had reported: the
   backend stores MAVLink ATTITUDE pitch unmodified and Cesium's positive pitch is also nose-up,
   but the view negated it, so a climb rendered as a dive. Roll was correct. Pose maths extracted
   to exported `aircraftHpr`/`aircraftQuaternion` and pinned by 12 tests (42 frontend total),
   both fixes mutation-checked. **Still worth 30 seconds of Tabor's eyes** for what a test cannot
   see: that the model reads as an aircraft (fin up, proportions) and that the CHASE camera frames
   it sensibly — the camera uses a different Cesium API (`HeadingPitchRange`, north-referenced)
   that no unit test here covers.
4. **Render the planner's turn-geometry numbers (seam S5) — UNCLAIMED, and the third instance
   of this exact bug in one day.** Every plan reports `stats.turn_bank_deg` / `turn_bank_ok` /
   `turn_radius_m` / `turn_max_speed_ms` and nothing renders any of them — including
   `turn_bank_ok: false`, the narrow-field case where the operator most needs telling. Same
   shape as items 1 and 2: a complete, tested backend surface with no caller.

### OPS — customer site, packaging, tooling
*Goal: it runs without a terminal.*
- **Session:** — · **Working on:** —

1. **Auto-connect.** Greenfield: no VID matching anywhere in the backend. Enumerate ports, match the
   Cube by `VID_2DAE`, connect without asking. Router + frontend only — if it needs a
   `vehicle_manager.py` change, **stop and open Seam S4**.
2. **Rebuild `AgOpsGCS.exe`** — it does not exist on this machine and the last build predates the
   hardening pass. A Cube bench day currently has nothing to run. Take the `exe-build` resource.

### DOCS
*Root design + reference docs. Small, and easy to park.*
- **Session:** — · **Working on:** —

---

## Seam register

Anything spanning two areas. **Owner = whoever must make the final connection.** A seam isn't done
until its owner has run the connected path end to end.

| # | Seam | Owner | State |
|---|---|---|---|
| **S1** | **Keepout payload shape.** PLANNER emits `keepouts: list[list[LatLon]]` + `keepout_buffer: float` (`routers/coverage.py:45-48`); AIR uploads exactly that to the FC as exclusion fences. Change the shape and AIR's upload breaks silently. | PLANNER announces, AIR consumes | OPEN |
| **S2** | **Bank limit agreement.** PLANNER constrains planned turn geometry; AIR's guardian bank monitor warns on measured bank. **Bravo has proposed a number: planner commands ≤ 25° (`coverage.DEFAULT_MAX_BANK_DEG`), sitting under guardian's 31.5° low-altitude threshold (`bank_warn_deg 45 × bank_low_alt_factor 0.7`, and a spray pass is entirely below `bank_low_alt_m`). The 6.5° gap is the margin for L1 overshoot, wind gradient and gusts — plan to the monitor's threshold and every headland trips it.** Rationale in the decisions log. | AIR | **ACCEPTED by AIR 2026-08-19** -- numbers verified against `guardian.py` defaults, not taken on trust |
| **S3** | **Altitude semantics.** `CoverageRequest.alt` defaults to **100 m**, a known-wrong placeholder; real spray is **10–25 m AGL**. AIR's terrain following makes altitude mean AGL rather than relative-to-home. **RULED 2026-08-19 (AIR):** a spray-plan `alt` means **metres AGL**. Mission items ship `MAV_FRAME_GLOBAL_RELATIVE_ALT` today, which equals AGL only while the ground is level with home -- true enough at Sabetha, false as a general contract, and SPRAY-FLIGHT-SAFETY #8 already flags that the flat-Kansas assumption is unwritten. AIR will move spray legs to `MAV_FRAME_GLOBAL_TERRAIN_ALT` with terrain following. The 100 m default is wrong under either reading; **PLANNER owns changing the number** (20 m suggested), AIR owns the frame. | AIR + PLANNER jointly | SEMANTIC SETTLED, number is PLANNER's |
| **S4** | **Auto-connect vs `vehicle_manager.connect`.** OPS may only add router/frontend logic. The moment it needs a change inside `vehicle_manager.py`, this seam opens and AIR makes that change. | AIR | NOT TRIGGERED |
| **S5** | **Turn-geometry readout.** Every plan now carries `stats.turn_bank_deg`, `turn_bank_ok`, `turn_radius_m` and `turn_max_speed_ms`. On a field too narrow to satisfy the limit `turn_bank_ok` is **false** and nothing shows it — the operator is told the plan is fine when it still commands 30–60° at spray height. Additive keys, so nothing breaks by ignoring them; the safety value is only realised when they render. Suggested minimum: surface `turn_bank_ok: false` next to the existing coverage/hazard warnings, with `turn_max_speed_ms` as the fix (“fly this field at ≤ X m/s”). | UI | OPEN — raised by bravo, PLANNER work complete |
| **S6** | **Headland overspray is on by default and the operator cannot see or refuse it.** Pass widening now lets spray reach up to half a swath past the field boundary (bounded, deliberate — it buys ~0.8 acres per traced 40-acre field). Both coverage endpoints accept `headlands: false` to turn it off, and nothing in the UI offers that. It matters at a real boundary: an organic neighbour, a road, a waterway. `stats.headland_passes` / `headland_extra_m` say whether a given plan actually widened anything. Suggested: a spray-settings toggle, defaulted on, worded as coverage-vs-overspray rather than as a geometry option. | UI | OPEN — raised by bravo, PLANNER work complete |

---

## Decisions log (append-only)

Newest at the bottom. One row per decision no session may silently reverse. Reversing one means a
**new row saying what it supersedes** — never deleting the old one. Tabor-authorised overlaps
(`claim.py grant`) get a row here too.

| Date | Decision | By | Supersedes |
|---|---|---|---|
| 2026-08-19 | **The radio link is supervisory, not a control link.** The aircraft flies and safely aborts the entire mission with zero contact. Link loss is routine, not an emergency. Everything in AIR's queue follows from this. | Tabor + Claude | — |
| 2026-08-19 | **Guardian belongs onboard, eventually.** Every monitor runs on the laptop today and goes silent exactly when the link drops. `guardian.py` is pure logic with full unit coverage, so it ports to a companion computer without a rewrite. This reframes M7 as the air/ground split, not generic cleanup. | Tabor + Claude | — |
| 2026-08-19 | **AIR ships no frontend.** Enforcement is wired backend-side so no AIR feature depends on a UI change from another session. Direct consequence of the `86c6a6e` cross-lane bug. | Claude | — |
| 2026-08-19 | **Overlap prevention is mechanical, not advisory** — claim registry + PreToolUse hook, deny-by-default once 2+ sessions are live, overrides only via Tabor's token. Replaces the two-lane honour system this file started as. | Tabor + Claude | the original 2-lane LANES.md |
| 2026-08-19 | **The planner commands at most 25° of bank; guardian keeps warning at 45° (31.5° low).** Two numbers on purpose, not a disagreement: the planner's is what the mission may *ask for*, the monitor's is what the aircraft must never *reach*. Planning to the monitor's threshold would trip it on every headland, so the planner sits a 6.5° margin under it for L1 overshoot, wind gradient and gusts. `coverage.DEFAULT_MAX_BANK_DEG = 25.0`. **Proposed by bravo (PLANNER); AIR owns S2 and may supersede this row.** | Claude (bravo) | — |
| 2026-08-19 | **Turn geometry is bought with path length, not with coverage.** Widening every turn costs +19% to +38% flight time on real field shapes, and the planner spends it by default rather than asking. A spray pass has no recovery altitude for a stall-spin, and the alternative was commanding 73° the airframe cannot fly. Not a silent trade: `max_bank_deg=0` restores the old geometry, and the achieved numbers ride in every plan's stats. **If Tabor decides the battery cost is unacceptable, change the ONE default — do not reintroduce adjacent-line ordering.** | Claude (bravo) | — |
| 2026-08-19 | **A plan that cannot meet the bank limit is still returned, with the truth attached.** Narrow fields are bounded by their own width, so the planner flies the widest turns available and reports `turn_bank_ok: false` + `turn_max_speed_ms`. Deliberately NOT a fail-closed 409 like hazard crossings: that pattern needs a client-side opt-in flag, which would put PLANNER's safety gate behind a UI change owned by another lane — the `86c6a6e` bug shape. Reporting is owned end-to-end by the planner; **S5** asks UI to render it. | Claude (bravo) | — |
| 2026-08-19 | **All three sessions share ONE working tree — the registry stops overlapping EDITS, not overlapping FILES.** No worktrees are in use, so every session sees every other session's uncommitted files on disk. Two consequences the guard cannot catch: `git add -A` will happily stage another lane's work-in-progress (**stage explicit paths, never `-A`**), and a test-count or suite-green claim silently includes whatever the other lanes have on disk at that moment — say whose tests are in the number. Found by bravo when a 409-test run stayed 409 across a rebase that added 12 tests: alpha's file had been visible all along. | Claude (bravo) | — |
| 2026-08-19 | **Headlands are closed by widening passes, never by a perimeter lap.** A boundary ring's corners are the field's own corners — 90° turns at a point, which is precisely the unflyable geometry the turn constraint exists to remove, plus an extra lap of flight time. Widening each pass to cover its own swath-deep band adds no passes and no turns and gets 97.6% to 99.6% coverage for +3.2% path. **Anyone reaching for a perimeter lap later is re-opening a settled question and must read the turn-geometry section first.** | Claude (bravo) | — |
| 2026-08-19 | **Bounded overspray past the field boundary is accepted by default.** Widening lets spray reach up to half a swath outside the boundary where an edge slants away — provably bounded, and the same overhang the centred sweep grid already produced in the other axis. Accepted because the alternative is leaving ~0.8 acres unsprayed per traced 40-acre field. `headlands=false` on both coverage endpoints where it is unacceptable: organic neighbour, road, waterway. | Claude (bravo) | — |
| 2026-08-19 | **A keepout buffer outranks coverage.** The same band logic would say a pass should also reach nearer a keepout than the clip allows, closing the remaining ~0.17 acres. Not done: the buffer is a chemical-drift guarantee with tests asserting every sprayed point respects it, and coverage is not worth eroding a standoff. The miss stays reported in `coverage_pct` rather than quietly sprayed. | Claude (bravo) | — |
| 2026-08-19 | **Terrain data is BUNDLED, not fetched.** Tabor's call. SRTM tiles ship with the aircraft rather than being pulled on demand, which follows directly from the standing decision that the radio link is supervisory: a ground station that needs the internet to know how high the ground is has re-introduced exactly the dependency the whole airframe design refuses. Costs repo/installer size and means coverage is limited to the tiles shipped. **Consequence that must be built in, not bolted on: planning or flying outside bundled coverage FAILS LOUD.** A missing tile must never degrade silently to no-terrain-awareness — that is the failure that puts the aircraft at 15 m AGL believing the ground is flat. | Tabor | — |
| 2026-08-19 | **S2 ACCEPTED: planner commands <= 25 deg bank; guardian warns at 31.5 deg below 30 m.** Verified rather than assumed -- `guardian.py` really does default `bank_warn_deg 45.0` x `bank_low_alt_factor 0.7` = 31.5 deg under `bank_low_alt_m 30.0`, and a 10-25 m spray pass sits entirely inside that band. 25 deg costs +5.0% stall speed (60 deg costs +41%), and `bank_sustained_s 2.0` means a gust overshoot must persist 2 s before it warns, so the 6.5 deg gap is wider in practice than on paper. | AIR (bravo proposed) | -- |
| 2026-08-19 | **The link is MAVLink 2** (`mavutil.set_dialect` at import in `vehicle_manager`). `mission_type` is a MAVLink 2 extension; on the old v10 bindings a fence or rally transfer is accepted as a REGULAR mission and silently overwrites the flight plan while reporting success. Proven live: 15 SITL scenarios green, and the onboard-fence scenario asserts the mission survives a fence upload. **Do not revert to v10 -- rally points need this too.** | AIR | -- |
| 2026-08-19 | **Only HAZARDS become hard onboard fences**, and the ring uploads RAW rather than buffered. Fencing spray-quality keepouts would fire FENCE_ACTION on a harmless sprayer-off overflight and could block RTL; the planner's buffer stays the soft standoff while the fence is the hard floor, matching the bench kit's GCS-warns-first ordering. Overflow REFUSES rather than truncates. | AIR | -- |
| 2026-08-19 | **The operator UI holds NO in-flight thresholds and computes NO readiness — it renders the server's verdict or admits it has none.** This is M6, and it had ALREADY been silently reversed: the pre-flight panel fell back to two locally-invented checks (link + GPS) whenever the server poll had not landed, so with a connection and a GPS fix it displayed a PASS while the gate was unreachable and would have refused to arm. A UI that disagrees with the gate is worse than one that admits ignorance, so absent a verdict it now reads CHECKING, never a pass. **Anyone adding a client-side threshold, a fallback verdict, or a "sensible default" to an operator panel is re-opening this.** Pinned by tests that fail if a no-verdict state renders as ready. | Claude (charlie) | — |

---

## Field notes for a redesign (written 2026-08-19, after the first real 3-session run)

This design was built and then immediately used by three concurrent sessions for a day. It held,
but not without lessons. If you are replacing it, these are the parts that cost real time.

**What broke**

1. **Identity is the hard problem, and it is silent when wrong.** A session does not know its own
   `session_id`. Claim under any other string and the guard blocks you from *your own* files, with a
   message naming a session that does not exist. Solve identity first, before ownership. The
   `SessionStart` hook telling a session its id is the clean fix; the `CLAIM_WHOAMI` handshake exists
   only to rescue sessions that were already running.
2. **Enforcement must live where sessions actually start.** A project-level hook in
   `<repo>/.claude/settings.json` does not load for a session whose project dir is somewhere else --
   alpha ran from the home directory and was completely unguarded for an hour while believing it was
   protected. Belief in protection you do not have is worse than knowing you have none.
3. **False positives are the dominant failure mode -- three in one session**, each blocking work on
   files that session owned: ASCII arrows (`->`) parsed as output redirection; heredoc *bodies*
   scanned for targets, so a commit message that merely NAMED a file read as writing it; and
   relative paths resolved against the session cwd rather than whatever the command `cd`'d into. A
   guard that blocks legitimate work trains sessions to route around the mechanism, which leaves you
   worse off than no guard. **Bias hard toward allowing.**
4. **Shell command strings are a bad ownership signal.** Structured tool inputs (`Edit`/`Write`
   `file_path`) are exact and trivial; `Bash` is a string you must parse, and every parse is wrong in
   some case. If starting over: enforce hard on structured inputs, treat shell as best-effort
   advisory, and accept that `rm` slips through occasionally rather than blocking real work daily.

**What worked and is worth keeping**

- **Inert below two sessions.** Solo work paid zero friction. Nobody had to opt out.
- **Heartbeat-by-use.** The guard renews the caller's claim on every edit, so liveness is free and a
  dead session's area frees itself. No stale locks, no manual cleanup, ever.
- **Overlap computed against the real file list**, not eyeballed -- it named all 43 contested files
  the instant a second session tried to claim a taken area.
- **Read-only access across areas stayed open**, and that mattered constantly: AIR read `coverage.py`,
  `gis_zones.py` and `keepout_watch.py` all day to build against them correctly.
- **Two-layer split.** A machine registry for mechanics (who owns what, is it live) and a markdown
  file for the things no tool can infer -- the seam register and the decisions log. Do not try to
  make one artifact do both.
- **Seams need named owners.** File partitioning is necessary and *not sufficient*: the `86c6a6e`
  class of bug lives precisely between two green, individually-correct areas. The seam register is
  what caught S1-S6 this run.
- **Stage by explicit path, never `git add -A`.** With concurrent uncommitted work in the tree, `-A`
  sweeps another session's half-finished code into your commit. This came up on every single commit.

**What was never solved**

- The registry stops accidents between cooperating sessions. It is **not** a security boundary: any
  agent with write access can edit `claim.py` itself. That was accepted deliberately, and the
  compensation is that overrides need a token only Tabor sets and every one is audited. A redesign
  should either accept the same limit honestly or move enforcement somewhere sessions cannot reach.

### Addendum to the field notes (bravo, PLANNER)

The section above says most of it and I agree with all of it — these are only the points it does
not already cover.

1. **Heartbeat-by-use fails for exactly the sessions that need it most.** It is listed above as a
   thing that worked, and it is, *provided the hook loads*. An unguarded session never renews, so
   its claim decays after 90 minutes while it is still actively working, and its area becomes
   claimable out from under it. Two of three sessions were in that state today. Liveness should not
   be a side effect of the enforcement path, because the sessions with no enforcement are the ones
   whose liveness you can least afford to guess at.

2. **The bootstrap trap: the command that fixes your claim was itself guarded.** Once `tools/` was
   claimed by another lane, `py tools\claim.py claim ...` was blocked — the path matched, and the
   guard's answer was "claim an area first." You cannot claim an area first; that is the command
   being blocked. Whatever replaces this needs its own control commands on an unconditional
   always-allow list, checked before anything else.

3. **One working tree is the deeper version of the `git add -A` problem.** Explicit pathspecs fix
   the commit, but the tree is still shared: a test run picks up every other lane's uncommitted
   code, so "419 green" is not a statement about your own change — it silently includes whatever
   else is on disk at that second. Per-session worktrees make both problems structural instead of
   procedural (rule 6's `node_modules` junction warning still applies). If worktrees stay off the
   table, then any test count quoted anywhere should name whose work was in the tree.

4. **Ownership is file-level; the real conflicts were concept-level.** PLANNER's commanded-bank
   ceiling and AIR's measured-bank monitor are two constants, in two lanes, that must agree — and
   nothing in the file partition hints at the relationship. The seam register caught it because a
   human wrote it down. "Who owns *bank*" is a question the tooling cannot currently ask, and three
   of the six seams this run were that shape.

5. **The lock machinery was never the binding constraint.** `sitl-5760` was never contended, no
   session ever needed an override grant, and file overlap was settled in the first minute. The work
   split cleanly on files and hard on *agreements*. Weight the redesign accordingly.

### Addendum (charlie, UI)

Both sections above are right and I am not repeating them. Five things neither covers.

1. **`release` is all-or-nothing per session, so a session that finishes one area cannot give it
   back.** I finished DOCS but was still working UI; `release --session <id> --area DOCS` is not a
   thing, and releasing would have dropped both. So I sat on DOCS — the file every other lane needs
   at close-out — purely because the tool had no way to hand back one area. Whatever replaces this
   needs per-area release, or claims scoped narrowly enough that holding one is harmless.

2. **The registry's label goes stale immediately and nothing notices.** Mine still read
   "post-flight scorecard panel" while I was three jobs further on. The registry is authoritative
   for *ownership* and quietly wrong about *purpose* — which is the half a human actually reads when
   deciding whether to interrupt a lane. Either make the label cheap to update (a `relabel` command)
   or stop displaying it as if it were current.

3. **A shared doc accumulates assertions that were true when written and are false when read.** I
   wrote "the guard does not load for charlie" in the Live board at 10:39. It was true. It was false
   an hour later, when someone fixed it — and my sentence sat there reading like current fact. Stale
   *state* in a shared board is more dangerous than no board, because it is written in the voice of
   the person who checked. Every Live-board claim about the *environment* (as opposed to the work)
   needs a timestamp and an expiry convention.

4. **The one file everybody writes is the one file nobody owns.** `LANES.md` is exempt from the
   guard by necessity, and that exemption is load-bearing — but it means the shared file is
   permanently dirty with two or three sessions' half-written blocks, and no session can commit it
   without sweeping in the others'. I twice left my own block uncommitted for exactly that reason.
   Per-session block *files* concatenated at read time, or a tiny append-only command, would remove
   the whole class.

5. **A coordination change silently corrupted a financial ledger, which is the kind of blast radius
   worth designing against.** `VALUATION.md`'s labour line sums each session's transcript wall clock
   — sound while sessions ran one at a time. Four overlapping sessions reported 36.7 h for a single
   calendar day; the honest union is 12.4 h. Appending the raw sum would have tripled the labour
   figure that anchors the number we ask Caleb for. Nothing in the coordination design knew it had a
   dependent downstream. When sessions stop being serial, go looking for everything that assumed
   they were.

**The headline, if only one thing survives:** the same bug shape appeared **three times in one day**
— the post-flight scorecard, the keepout-proximity monitor before it, and the turn-geometry stats
(seam **S5**, still open). Every instance was a complete, tested, green backend surface that no UI
ever called. File partitioning *causes* this: it cleanly assigns both halves of a feature to
different owners and assigns the seam between them to nobody. Partition by **feature**, with one
owner accountable end to end, or keep partitioning by file and treat the seam register as the
primary artifact rather than a supplement to it.

---

## Protocol

**Start**
1. `git -C <repo> pull`
2. `py tools\claim.py status -v` — see what's free.
3. Claim your area, then fill in your block above. Say in chat which area you took.

**During** — commit small and push. Other sessions pull to see your code; this board tells them why.

**Close**
1. Seam pass (rule 3). Update every seam you touched.
2. Unit tests green (`pytest`, `npm test` — neither needs a lock). SITL only while holding
   `sitl-5760`.
3. Append any new decision to the log.
4. Update `CLAUDE-CALEB.md`'s `▶ RESUME HERE` — re-read it immediately before writing, since another
   session may have just restructured it. (`DOCS` area — claim it or coordinate.)
5. `py tools\claim.py release --session <id>`, drop any resources, commit and push.

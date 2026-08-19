# LANES — parallel session coordination board

**Purpose:** two Claude sessions work this repo at the same time without colliding, duplicating,
or reverting each other. This file is the shared truth. Read it at session start, claim a lane,
update your own block as you go, and do the seam pass before you finish.

> ### 🔴 THE ABSOLUTE-PATH RULE — read this first
> Live coordination happens in **`C:\Users\jacks\rc-plane-app\LANES.md`** (the MAIN checkout),
> always by that absolute path — **even when your session is working inside a git worktree.**
> A worktree gets its own tracked copy of this file at its own path; that copy is **stale by
> design**. Never read or write the worktree-local copy, and only ever commit this file from the
> main checkout. Git is for durability across machines; the absolute path is for seeing each
> other *now*, without a commit/push/pull round-trip.

---

## Hard rules

1. **Edit only your own lane's block.** Never reformat, renumber, or "tidy" the other lane's
   section, the seam register, or another lane's decisions. If an `Edit` fails with *"file
   modified since read"*, re-read and retry immediately — the other session just wrote. This
   exact race bit us twice on `CLAUDE-CALEB.md`.
2. **Claim before you touch.** A file not listed under your lane in the ownership table is not
   yours. If you need one, add it to your block as a **claim request** and say so in your reply
   to the user — don't just take it.
3. **SITL is single-occupancy.** Port 5760 fits one process. Take the lock below before running
   `scenarios.ps1` or `pytest -m sitl`, release it after. **A red scenario while both sessions
   are active is far more likely contention than a regression — re-run it alone before believing
   it.** Plain `pytest` (unit, fakes only) and `npm test` need no lock and may run concurrently.
4. **Wire your own caller.** Never ship a backend surface whose only caller belongs to the other
   lane. That is precisely the `86c6a6e` cross-lane bug: a complete, tested, green keepout monitor
   that nothing ever armed, because the seam was owned by nobody. If your work genuinely needs the
   other lane's layer, it goes in the **seam register** with a named owner — it does not go in as
   an assumption.
5. **Seam pass before you close.** Endpoints with no caller. UI reading fields nothing sets.
   Defaults on both sides that must agree. Check all three, explicitly, every time.
6. **Solo-only work is off-limits during a parallel wave:** **M5** (mission model/resume) and
   **M7** (`vehicle_manager.py` restructure). `vehicle_manager.py` is 2,057 lines and 8 of 10
   routers import it — either one collides with everything. Wait for an exclusive session.
7. **Never junction (`mklink /J`) `node_modules` or any shared dir from a worktree.** Deleting the
   worktree follows the junction and wipes the *target*. It cost us the main checkout's
   `node_modules` on 2026-08-18. A worktree needing node deps runs its own `npm install` (~9 s).
8. **Append, never rewrite**, in the Decisions log. Reversing a decision means a new dated row
   that says what it supersedes — not deleting the old one.

---

## Lane ownership (this wave)

Disjointness verified against the file map in `CLAUDE-CALEB.md` and the 2026-08-18 parallelism note.

| | **Lane AIR** — onboard enforcement | **Lane GROUND** — planner + effortless layer |
|---|---|---|
| **Goal** | The aircraft survives and obeys with **zero link**. | The plan is flyable, and the app is effortless to use. |
| **Backend** | `app/vehicle_manager.py`, `app/routers/safety.py`, `app/routers/mission.py`, `app/guardian.py`, new `app/onboard_fence.py` | `app/coverage.py`, `app/coverage_multi.py`, `app/routers/coverage.py`, `app/routers/coverage_multi.py`, `app/routers/connection.py` |
| **Frontend** | **none** — Lane AIR ships no UI this wave | `frontend/src/**` (all of it, incl. `LogsPanel.jsx`, `LaunchControl.jsx`, `SprayPanel.jsx`) |
| **Tests** | `tests/test_guardian.py`, `tests/test_keepout_watch.py`, `tests/sitl/**` | `tests/test_coverage*.py`, `tests/test_reroute.py`, `tests/test_zones*.py`, `frontend/src/components/__tests__/**` |
| **Off-limits** | anything under `frontend/`, `coverage*.py` | `vehicle_manager.py`, `guardian.py`, `routers/safety.py`, `routers/logs.py`, `tests/sitl/` |

**Shared, read-only to both:** `README.md`, `ARCHITECTURE.md`, `GAP-ANALYSIS.md`, `VALUATION.md`.
**`CLAUDE-CALEB.md`:** append to *History* freely; edit the `▶ RESUME HERE` block **only at session
close**, and re-read immediately before writing it.

---

## Live board

### Lane AIR
- **Status:** UNCLAIMED
- **Session:** —
- **Last update:** —
- **Working on:** —
- **Claim requests:** —

**Queue (in order):**
1. **Polygon exclusion fences uploaded to the FC.** Greenfield — verified 2026-08-19: nothing in
   the repo sets `FENCE_TYPE` bit 4, `FENCE_TYPE_CIRCLE_ALT = 3` is alt+circle only, and keepouts
   exist *only* in the GCS. Upload the plan's keepout polygons over `MISSION_TYPE_FENCE` so they
   are enforced onboard with no link. Consume the payload named in **Seam S1**. Wire the caller
   inside `routers/mission.py` (rule 4) — no UI dependency.
2. **Terrain following.** Also greenfield: `guardian.py`'s docstring states outright that there is
   no terrain awareness. `terrain/N39W096.DAT` is already in the repo and unused. At 10–25 m AGL
   this is not optional.
3. **Rally points** — so a link-loss RTL diverts to a safe alternate instead of flying home
   *through* a mapped powerline. Only the `mission_rally` capability bit is decoded today.
4. SITL scenario proving each of the above with the link deliberately dead.

### Lane GROUND
- **Status:** UNCLAIMED
- **Session:** —
- **Last update:** —
- **Working on:** —
- **Claim requests:** —

**Queue (in order):**
1. **🔥 Turn-geometry bank constraint in `coverage.py`.** Still the highest-value open safety item.
   Measured: 50–65° banks in ordinary loiter/RTL turns, past `ROLL_LIMIT_DEG`, while a spray pass
   flies 10–25 m AGL where that bank has no recovery altitude. Detection exists; the planner must
   stop commanding them. Threshold agreement is **Seam S2**.
2. **Scorecard UI in `LogsPanel.jsx`.** Confirmed 2026-08-19: zero occurrences of `scorecard` in
   that file. The backend writes one on every disarm and serves it on `GET /api/logs/{name}`
   (+ `has_scorecard` in the list view) and no operator can see it. Min hazard distance, min RTL
   margin, max bank, warning counts per monitor. Pure consumer of an existing surface — no seam.
3. **Auto-connect.** Greenfield: no VID matching anywhere in the backend. Enumerate ports, match
   the Cube by `VID_2DAE`, connect without asking. Router + frontend only — if it needs a
   `vehicle_manager.py` change, **stop and open Seam S4** instead.
4. **One-verdict preflight.** `preflight.py` already computes blockers vs advisories server-side;
   collapse the UI to one state + one plain sentence, detail behind a disclosure triangle.
   Render-only — do not move logic into the client (that regresses M6).
5. **Headlands** — justified by measurement: 0.41 and 0.56 acres genuinely missed on real Sabetha
   fields. `coverage_pct` is the number that verifies the fix.

---

## 🔒 SITL lock (port 5760)

- **Holder:** FREE
- **Taken at:** —
- **Expected release:** —

Take it by setting Holder to your lane, stamp the time, run, then set it back to `FREE`. If it has
been held over ~30 min with no board update, assume the holder died and reclaim it — but say so in
your lane block. Default owner when both want it: **Lane AIR** (its work is SITL-bound; Lane
GROUND's is mostly unit-testable).

---

## Seam register

Anything that spans both lanes. **Owner = the lane that must make the final connection.** A seam is
not done until its owner has run the connected path end to end.

| # | Seam | Owner | State |
|---|---|---|---|
| **S1** | **Keepout payload shape.** Planner emits `keepouts: list[list[LatLon]]` + `keepout_buffer: float` (`routers/coverage.py:45-48`); Lane AIR uploads exactly that to the FC as exclusion fences. If Lane GROUND changes the shape, Lane AIR's upload breaks silently. | GROUND announces, AIR consumes | OPEN |
| **S2** | **Bank limit agreement.** Lane GROUND constrains planned turn geometry; Lane AIR's guardian bank monitor warns on measured bank. Two numbers that must agree, on opposite sides — the exact defaults-disagree failure class. Single source, named in the Decisions log. | AIR | OPEN |
| **S3** | **Altitude semantics.** `CoverageRequest.alt` defaults to **100 m**, a known-wrong placeholder; real spray is **10–25 m AGL**. Lane AIR's terrain following makes altitude mean AGL, not relative-to-home. Both lanes touch altitude meaning — settle it once, here, before either ships. | AIR + GROUND jointly | OPEN |
| **S4** | **Auto-connect vs `vehicle_manager.connect`.** Lane GROUND may only add router/frontend logic. The moment it needs a change inside `vehicle_manager.py`, this seam opens and Lane AIR makes that change. | AIR | NOT TRIGGERED |

---

## Decisions log (append-only)

Newest at the bottom. One row per decision that either lane must not silently reverse.

| Date | Decision | By | Supersedes |
|---|---|---|---|
| 2026-08-19 | **The radio link is supervisory, not a control link.** The aircraft flies and safely aborts the entire mission with zero contact. Link loss is routine, not an emergency. Everything in Lane AIR's queue follows from this. | Tabor + Claude | — |
| 2026-08-19 | **Guardian belongs onboard, eventually.** Every monitor currently runs on the laptop and goes silent exactly when the link drops. `guardian.py` is pure logic with full unit coverage, so it ports to a companion computer without a rewrite. This reframes M7 as the air/ground split, not generic cleanup — **do not start M7 during a parallel wave.** | Tabor + Claude | — |
| 2026-08-19 | **Lane AIR ships no frontend this wave.** Enforcement is wired backend-side so no Lane AIR feature can depend on a Lane GROUND UI change. Direct consequence of the `86c6a6e` cross-lane bug. | Claude | — |

---

## Protocol

**Starting a session**
1. `git -C C:\Users\jacks\rc-plane-app pull`
2. Read this file **by absolute path**. If both lanes are UNCLAIMED, ask the user which to take.
3. Claim: set Status to `ACTIVE`, add your session id and a timestamp (`date` in Bash), write what
   you're working on. Say in chat which lane you took.

**During**
- Update your block when you change what you're doing — not only at the end.
- Commit small and push. The other lane pulls to see your code; this board tells them to.

**Closing**
1. Seam pass (rule 5). Update every seam you touched.
2. Unit tests green. SITL only if you hold the lock.
3. Append any new decision to the log.
4. Update `CLAUDE-CALEB.md`'s `▶ RESUME HERE` — re-read it immediately before writing, because the
   other session may have just restructured it.
5. Set your lane back to `UNCLAIMED`, release the SITL lock, commit and push **this file from the
   main checkout**.

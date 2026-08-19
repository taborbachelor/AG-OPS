# LANES — parallel session coordination

**Any number of Claude sessions can work this repo at once without overlapping.** That guarantee is
enforced by a claim registry and a PreToolUse hook, not by everyone remembering to read a file.

- **Machine truth:** `tools/claim.py` + `.claim/` (gitignored runtime state).
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
- **Session:** `1681cef0` (alpha) · **Working on:** item 1, polygon exclusion fences uploaded to the
  FC. **AIR is live now, so S2/S3 have an owner** — bravo, don't wait on your proposed numbers, I'll
  accept or supersede them in the decisions log and say so here.

1. **Polygon exclusion fences uploaded to the FC.** Greenfield, verified 2026-08-19: nothing sets
   `FENCE_TYPE` bit 4 (`FENCE_TYPE_CIRCLE_ALT = 3` is alt+circle only), and keepouts exist *only*
   in the GCS. Upload the plan's keepout polygons over `MISSION_TYPE_FENCE` so they're enforced
   onboard with no link. Consume the payload in **Seam S1**; wire the caller in `routers/mission.py`.
2. **Terrain following.** Also greenfield — `guardian.py`'s docstring says outright there's no
   terrain awareness, and `terrain/N39W096.DAT` sits unused. At 10–25 m AGL this is not optional.
3. **Rally points** — so a link-loss RTL diverts to a safe alternate instead of flying home
   *through* a mapped powerline. Only the `mission_rally` capability bit is decoded today.
4. A SITL scenario proving each of the above with the link deliberately dead.

### PLANNER — coverage, GIS, hazards
*Goal: the plan is flyable.*
- **Session:** `c63d3a97` (bravo) · **Working on:** item 1, the turn-geometry bank constraint
  — **SHIPPED**, see below. **S2 answered with a number and a measurement** (decisions log);
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
2. **Headlands** — justified by measurement: 0.41 and 0.56 acres genuinely missed on real Sabetha
   fields. `coverage_pct` verifies the fix.

### UI — GCS operator frontend
*Goal: effortless.*
- **Session:** `b06ca0f4` (charlie) · **Working on:** item 1, the scorecard panel — **SHIPPED**,
  see below. ⚠️ **The PreToolUse guard does not load for charlie.** It is wired in the repo's
  `.claude/settings.json`, but charlie's project dir is the home directory, not the repo, so
  repo-level project settings never apply. Charlie holds the UI boundary by discipline, not by the
  hook — and so does any other session started from the home directory rather than the repo.

1. ~~**Scorecard UI in `LogsPanel.jsx`.**~~ **DONE 2026-08-19 (charlie).** New
   `components/Scorecard.jsx` rendered from the playback view, a `scorecard` badge on list rows
   that have one, 8 new tests (24 frontend total), both invariants mutation-checked. It holds **no
   thresholds** — the card carries none and M6 keeps threshold judgement in the guardian, so the
   only thing coloured is the guardian's own per-monitor warning counts. Original finding: The backend writes one on every disarm and serves it on `GET /api/logs/{name}`
   (+ `has_scorecard` in the list view), and no operator can see it. Min hazard distance, min RTL
   margin, max bank, warning counts per monitor. Pure consumer of an existing surface — no seam.
2. **One-verdict preflight.** `preflight.py` already computes blockers vs advisories server-side;
   collapse the UI to one state plus one plain sentence, detail behind a disclosure triangle.
   Render-only — do **not** move logic into the client, that regresses M6.
3. 3D scene eyeball check (needs Tabor's eyes): `start-all.ps1` → FLY view → does the nose lead the
   trail? If sideways, adjust the heading offset in `MapView3D.jsx`'s `oriProp`.

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
| **S2** | **Bank limit agreement.** PLANNER constrains planned turn geometry; AIR's guardian bank monitor warns on measured bank. **Bravo has proposed a number: planner commands ≤ 25° (`coverage.DEFAULT_MAX_BANK_DEG`), sitting under guardian's 31.5° low-altitude threshold (`bank_warn_deg 45 × bank_low_alt_factor 0.7`, and a spray pass is entirely below `bank_low_alt_m`). The 6.5° gap is the margin for L1 overshoot, wind gradient and gusts — plan to the monitor's threshold and every headland trips it.** Rationale in the decisions log. **AIR: accept or supersede.** | AIR | PROPOSED, awaiting AIR |
| **S3** | **Altitude semantics.** `CoverageRequest.alt` defaults to **100 m**, a known-wrong placeholder; real spray is **10–25 m AGL**. AIR's terrain following makes altitude mean AGL rather than relative-to-home. Settle it once, here, before either ships. | AIR + PLANNER jointly | OPEN |
| **S4** | **Auto-connect vs `vehicle_manager.connect`.** OPS may only add router/frontend logic. The moment it needs a change inside `vehicle_manager.py`, this seam opens and AIR makes that change. | AIR | NOT TRIGGERED |
| **S5** | **Turn-geometry readout.** Every plan now carries `stats.turn_bank_deg`, `turn_bank_ok`, `turn_radius_m` and `turn_max_speed_ms`. On a field too narrow to satisfy the limit `turn_bank_ok` is **false** and nothing shows it — the operator is told the plan is fine when it still commands 30–60° at spray height. Additive keys, so nothing breaks by ignoring them; the safety value is only realised when they render. Suggested minimum: surface `turn_bank_ok: false` next to the existing coverage/hazard warnings, with `turn_max_speed_ms` as the fix (“fly this field at ≤ X m/s”). | UI | OPEN — raised by bravo, PLANNER work complete |

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

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
- **Session:** — · **Working on:** —

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
- **Session:** — · **Working on:** —

1. **🔥 Turn-geometry bank constraint in `coverage.py`** — highest-value open safety item. Measured:
   50–65° banks in ordinary loiter/RTL turns, past `ROLL_LIMIT_DEG`, while a spray pass flies
   10–25 m AGL where that bank has no recovery altitude. Detection exists; the planner must stop
   commanding them. Threshold agreement is **Seam S2**.
2. **Headlands** — justified by measurement: 0.41 and 0.56 acres genuinely missed on real Sabetha
   fields. `coverage_pct` verifies the fix.

### UI — GCS operator frontend
*Goal: effortless.*
- **Session:** — · **Working on:** —

1. **Scorecard UI in `LogsPanel.jsx`.** Confirmed 2026-08-19: zero occurrences of `scorecard` in
   that file. The backend writes one on every disarm and serves it on `GET /api/logs/{name}`
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
| **S2** | **Bank limit agreement.** PLANNER constrains planned turn geometry; AIR's guardian bank monitor warns on measured bank. Two numbers on opposite sides that must agree — the exact defaults-disagree failure class. One source of truth, named in the decisions log. | AIR | OPEN |
| **S3** | **Altitude semantics.** `CoverageRequest.alt` defaults to **100 m**, a known-wrong placeholder; real spray is **10–25 m AGL**. AIR's terrain following makes altitude mean AGL rather than relative-to-home. Settle it once, here, before either ships. | AIR + PLANNER jointly | OPEN |
| **S4** | **Auto-connect vs `vehicle_manager.connect`.** OPS may only add router/frontend logic. The moment it needs a change inside `vehicle_manager.py`, this seam opens and AIR makes that change. | AIR | NOT TRIGGERED |

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

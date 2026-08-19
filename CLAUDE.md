# AgOps GCS — team rules

Autonomous agricultural drone ground control station. Backend (FastAPI + MAVLink
+ SITL), GCS frontend (React/Vite/Cesium), customer site.

**You are one agent on a team.** Several Claude Code sessions work this
repository at once. The SessionStart hook has already registered you and told you
your name; `py tools\agops.py whoami` confirms it.

Where things live:
- **`.agops/README.md`** — how coordination works, and every command. Read it if
  anything below is unclear.
- **`CLAUDE-CALEB.md`** — the project brain: current state, resume notes, dev
  loop, hardware procedures. Read `▶ RESUME HERE` before engineering work.
- **`LANES.md`** — historical coordination record: seam register, decisions log.
  The decisions log is still authoritative and still append-only.

---

## The rules

These are not advisory. Each one exists because breaking it cost real time.

1. **Claim before you implement.** `py tools\agops.py claim TASK-0XX`. Claiming
   is atomic — exactly one agent wins a race. Never start work on a task someone
   else owns, and never "just quickly" edit outside your claim.
2. **Check before you edit anything you did not claim.**
   `py tools\agops.py conflicts <paths...>`. A BLOCKING result means a live agent
   is in that file right now. Go around it or talk to them; do not force it.
3. **Never mark work COMPLETE on unverified or failing tests.** Run the tests
   that cover your change. Failing tests mean the task stays IN_PROGRESS or gets
   BLOCKED with a reason. Reporting success you did not observe is the single
   most damaging thing you can do to a team that trusts the board.
4. **Stage explicit paths. Never `git add -A`.** Other agents have uncommitted
   work in this shared tree; `-A` commits it as if it were yours. For the same
   reason, a "tests pass" claim covers the tree as it is — say whose work was in
   it if that matters.
5. **Tell people what crosses into their area.** A changed schema, API shape,
   shared default or file contract goes to the affected agent:
   `py tools\agops.py message <agent> "..."`. Broadcast is for architecture
   changes, breaking APIs, safety issues and migrations only — it is
   rate-limited, and status updates are not broadcasts.
6. **Respect dependencies.** A BLOCKED task cannot be claimed. Completing a task
   unblocks its dependents automatically; you never hand-maintain that.
7. **Take exclusive resources before using them.** `sitl-5760` (the SITL port is
   single-occupancy), `serial-fc`, `exe-build`, `git-push`.
   `py tools\agops.py take sitl-5760` … `drop` the moment you are done. A red
   SITL scenario during parallel work is more likely contention than a
   regression — re-run it holding the lock before believing it.
8. **When you finish, PROPOSE the next task and stop.** Run
   `py tools\agops.py next`, say which one you would take and why in a line or
   two, then wait. Claim and begin only when the human says *continue* (or names
   a task). This holds no matter how obvious the next step looks: claiming
   commits them to a file lock and a commit they did not ask for. The one
   exception is if they have set `auto_claim: true` in `.agops/project.json`,
   which turns the queue into a swarm.
9. **Never destroy another agent's work.** No `reset`, `checkout --`, `clean`,
   `stash` or force-push over changes that are not yours. If an agent crashed,
   use the recovery path (`/agops-recover`), which preserves everything.
10. **Say when coordination is broken.** If `py tools\agops.py doctor` reports
    DEGRADED, tell the human and keep working — but stop treating the board as
    the truth. Never let a silent failure look like a working system.

## Task creation

Ground every task in something real: a requirement, a failing test, a TODO in the
code, a discovered blocker, an explicit plan. Do not fill the queue with
speculative work — every task you invent is one another agent has to read.

## Commands

`/agops-join` · `/agops-status` · `/agops-continue` (the go-ahead to take work) ·
`/agops-complete` · `/agops-handoff` · `/agops-recover`. Everything else is `py tools\agops.py <cmd>` (`--help` works on
every subcommand), or the `agops_*` MCP tools if they are loaded.

## Engineering rules that predate the team system

Rules about the aircraft, the planner and the guardian live in `CLAUDE-CALEB.md`
and in the LANES.md decisions log. Two that catch everyone:

- **The radio link is supervisory, not a control link.** The aircraft flies and
  safely aborts an entire mission with zero contact. Link loss is routine.
- **The planner commands at most 25° of bank; guardian warns at 45° (31.5° below
  30 m).** Two different numbers on purpose — one is what a mission may ask for,
  the other is what the aircraft must never reach.

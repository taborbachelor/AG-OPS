# AgOps coordination

How several Claude Code sessions work this repository at once without colliding.
Written for the next agent as much as for a human.

---

## 1. Architecture

Four questions, four mechanisms, deliberately not conflated:

| Question | Mechanism | Where |
|---|---|---|
| What is the code, and its history? | **Git** | `.git` — unchanged, still the only source of truth for code |
| Who is doing what? | **Tasks** | `.agops/agops.db` → `tasks` |
| What do I need to tell someone? | **Messages** | `.agops/agops.db` → `messages` |
| What am I allowed to edit? | **Ownership** | task `affected_files` + areas, enforced by a PreToolUse hook |
| What is happening overall? | **Project status** | `py tools\agops.py status` |

```
                    AgOps GCS repository
                             |
              .agops/agops.db  (SQLite, WAL, local)
                             |
   +-------------+-----------+-----------+-------------+
   |             |                       |             |
 alpha         bravo                  charlie        delta ...
   |             |                       |             |
   +--------- shared task queue, messages, ownership ---+
                             |
             TASK-001    TASK-002    TASK-003
                 \           |
                  \      depends on
                   \         |
                    +---> TASK-004  BLOCKED
                              |
                    prerequisites COMPLETE
                              |
                          AVAILABLE -> next agent claims atomically
```

Two interfaces over one core, on purpose:

- **MCP** (`.mcp.json` → `tools/agops/mcp_server.py`) — typed `agops_*` tools the
  model calls directly. Project-scoped, so it only ever loads in this repo.
- **CLI** (`py tools\agops.py`) — the same operations for humans, hooks, and as
  the fallback when MCP is unavailable.

**Failure mode:** if any of this breaks, Claude Code still works. Every hook
fails open, the MCP server is optional, and Git does not depend on any of it.
`py tools\agops.py doctor` reports health honestly; a degraded coordinator says
so rather than pretending.

## 2. Project identity and isolation

`project_id: agops-gcs`, set in `.agops/project.json`.

Isolation is enforced twice, independently:

1. **Structurally** — coordination state is a file inside this repository, and
   `.mcp.json` is project-scoped. A session in another checkout cannot see this
   team at all.
2. **Explicitly** — every core operation takes an optional `project` argument
   and refuses on mismatch (`project mismatch: this repository is 'agops-gcs'`).

## 3. Agents

Registration is automatic: the SessionStart hook registers the session, using the
**Claude `session_id`** as the agent id. That matters — identity is solved before
ownership, because an agent that claims under a guessed id gets locked out of its
own files by its own claim.

Names come from the NATO alphabet in order (`alpha`, `bravo`, `charlie`,
`delta`, …). Existing names are never reassigned, and re-registering the same
session re-attaches instead of forking a second agent.

Statuses: `STARTING IDLE WORKING BLOCKED REVIEWING WAITING OFFLINE ERROR`.

```
py tools\agops.py register --name delta --specialty ui --specialty testing
py tools\agops.py whoami
py tools\agops.py agents
py tools\agops.py heartbeat --status WORKING
```

Specialties are a **ranking signal only**, never a restriction — a team where
only one agent may touch an area stalls the moment that agent is busy.

## 4. Tasks

Statuses: `PENDING AVAILABLE IN_PROGRESS BLOCKED REVIEW COMPLETE CANCELLED`.
Priorities: `CRITICAL HIGH MEDIUM LOW`.

```
py tools\agops.py create "Rebuild AgOpsGCS.exe" --priority HIGH ^
    --area OPS --file backend/AgOpsGCS.spec --depends-on TASK-003
py tools\agops.py tasks --status AVAILABLE
py tools\agops.py task TASK-007
```

A task with unmet dependencies is `BLOCKED` and cannot be claimed. Completing a
task recomputes its dependents automatically and tells you what it unblocked —
dependency state is derived, never hand-maintained.

## 5. Claiming (the atomic part)

```
py tools\agops.py claim TASK-007
```

The guarantee is one conditional UPDATE inside an IMMEDIATE transaction:

```sql
UPDATE tasks SET owner=?, status='IN_PROGRESS'
WHERE task_id=? AND owner IS NULL AND status='AVAILABLE'
```

No agent ever reads-then-writes, so there is no window where two see it free.
The loser gets `TASK-007 is already owned by alpha.` and cannot act as owner:
`complete` and `release` re-check ownership and refuse. Six concurrent OS
processes racing one claim is a test in the suite, not a hope.

## 6. Who decides what an agent works on

**Tabor dispatches; agents do not self-serve.** `claim_policy` in
`.agops/project.json` defaults to `assigned`, and an unforced `claim` is refused:

```
py tools\agops.py assign TASK-005 charlie --note "exe-build lock is free"
```

That sets the owner, marks it IN_PROGRESS and messages the agent, who picks it up
on its next turn. An agent told directly to start uses `claim --force`, which
records that a human authorised it.

Why this is the default: the problem this system was built for is three sessions
landing on one task. Dispatch solves that without giving up control of what gets
built next, which is the part a human is actually good at and wants to keep.

```
py tools\agops.py admin policy assigned      # default: Tabor dispatches
py tools\agops.py admin policy on_request    # agents may claim when told to
py tools\agops.py admin policy self_serve    # swarm: agents pull from the queue
```

**Agents do not pick up work on their own.** `auto_claim` in
`.agops/project.json` is **false** by default: an idle agent surfaces the ranked
queue, recommends one, and waits. It claims and starts only when the human says
*continue* or runs `/agops-continue`.

That default was earned. A `claude -p` session asked only for a status report
claimed a task, implemented it, and committed it. The code was fine; nobody had
asked for it. Proposing costs a sentence -- claiming commits an agent, a file
lock and a commit the human did not agree to.

```
py tools\agops.py admin auto-claim on     # swarm mode: agents self-dispatch
py tools\agops.py admin auto-claim off    # default: propose and wait
```

The brake is on the agent's *initiative*, not on the mechanism -- claiming stays
instant the moment a human asks for it.

## 6b. Ranking

```
py tools\agops.py next
```

Ranked by priority → conflict-freedom → specialty match → age. Use it instead of
searching the repo for something to do. The Stop hook surfaces the same list
automatically whenever you finish with no task held.

## 7. Ownership and conflicts

```
py tools\agops.py conflicts backend/app/coverage.py frontend/src/App.jsx
py tools\agops.py owners backend/app/coverage.py
```

| Level | Meaning | Effect |
|---|---|---|
| `BLOCKING` | a **live** agent's IN_PROGRESS task lists an overlapping path | claim refused; PreToolUse blocks the write |
| `WARNING` | the path is in an area someone is working in | advisory only, never blocks |
| `NONE` | nobody is near it | free |

Deliberate limits, so the guard stays credible:
- solo sessions are never guarded (fewer than two live agents = nobody to hit);
- a stale or offline agent's files block nobody;
- reads across areas are always allowed;
- file-level conflicts outrank directory-level advisories;
- `always_open` paths (`LANES.md`, `CLAUDE.md`, `.agops/*`) are never guarded.

If you and another agent have genuinely agreed to share a file, `claim --force`
records that decision rather than hiding it.

## 8. Messaging

```
py tools\agops.py message bravo "waypoint alt is metres AGL now" --type WARNING --task TASK-012
py tools\agops.py broadcast "mission schema v2 lands today" --type WARNING
py tools\agops.py inbox
```

Types: `INFO QUESTION WARNING BLOCKER HANDOFF REVIEW_REQUEST COMPLETION`.
Broadcasts are rate-limited per sender (default 300 s) — they are for
architecture, breaking APIs, safety and migrations, not status. Read state is
per recipient, so one agent reading a broadcast does not consume it for the rest.
The Stop hook delivers unread messages automatically.

## 9. Handoffs

```
py tools\agops.py handoff TASK-012 charlie --state "..." --changed "..." ^
   --remaining "..." --problems "..." --tests "..." --next "..." --file src/x.py
```

Ownership moves with the message. Omitting the problems is worse than no handoff.

## 10. Completion and Git

Git stays the source of truth. Completion requires a real summary and refuses
outright if you report failing tests.

```
py tools\agops.py complete TASK-012 "widened passes; 419 tests green" ^
   --tests-passed --commit 29a79ba
```

**Stage explicit paths, never `git add -A`** — the working tree is shared, and
`-A` commits another agent's half-finished work as if it were yours.

## 11. Staleness and recovery

A quiet agent is usually thinking. Stale = no heartbeat for 45 min (configurable);
presumed gone at 4 h. Heartbeats refresh from several independent paths (the
guard, the Stop hook, every CLI call) so liveness never depends on one of them
firing.

```
py tools\agops.py recover              # dry run: who is stale, what they hold, git state
py tools\agops.py recover --apply      # flag their tasks REVIEW + needs_recovery
py tools\agops.py reclaim TASK-012 --verified
```

Recovery **never** frees a task automatically, deletes anything, or touches git.
`--verified` is you asserting you actually looked. Session end marks an agent
OFFLINE and frees its resource locks, but **preserves task ownership** — a closed
terminal is not evidence the work is finished.

## 11b. The live board

Keep this open in its own terminal for the whole work session:

```
tools\monitor.cmd                     (double-click works too)
py tools\agops.py monitor --watch     (same thing, 2s refresh)
py tools\agops.py monitor --watch 10  (slower, for a second screen)
```

It shows every session (live / stale / offline), what each holds, IN PROGRESS
with who took it and when, the OPEN queue, what is BLOCKED and on what, and
COMPLETE with the commit **and whether it is PUSHED, LOCAL ONLY, NOT IN REPO or
has no commit at all**. That last column is the point: "complete" is a claim
about the board, "pushed" is a claim about the world, and they come apart.

It repaints in place rather than clearing the screen each frame, because a board
that strobes once a second is one you stop looking at. If the coordination store
is briefly unreadable it says so on the board and keeps polling rather than
taking the window down.

**A Claude slash command cannot do this.** A session runs a command, gets output
and returns -- it has nowhere to keep repainting. `/agops-monitor` gives a
one-shot snapshot and points here for the live view.

## 12. Human override

```
py tools\agops.py admin pause                    # coordination inert, work normally
py tools\agops.py admin resume
py tools\agops.py admin enforcement off          # advisory | blocking | off
py tools\agops.py admin assign bravo --task-id TASK-012
py tools\agops.py admin cancel --task-id TASK-012
py tools\agops.py admin clear-locks
py tools\agops.py events --limit 40              # who did what, when
```

## 13. Starting a new agent

Open Claude Code in this repository. That is the whole procedure — the
SessionStart hook registers the session, assigns the next NATO name, and briefs
it on the team, the occupied files, the available work and the commands.

Optional, and worth it: `claude --session-id <uuid>` makes the identity
deterministic across restarts, and `claude --worktree <name>` gives the agent its
own working tree.

Then in the session: `/agops-join` (optionally with specialties).

## 14. Worktree strategy

**Chosen: shared working tree by default, worktrees available per agent.**

Why not force worktrees: the repo currently has one checkout with a Python venv,
two `node_modules` trees and a SITL install, and migrating live sessions into
worktrees mid-flight would disrupt work in progress for a problem the ownership
guard already handles. Worktrees also cost a fresh `npm install` per tree, and a
junctioned `node_modules` once destroyed the main checkout's copy — never
junction shared directories into a worktree.

Why they are still available: a shared tree means `git add -A` can sweep up
another agent's work and a test run silently includes it. Use
`claude --worktree <name>` when an agent will be doing wide, long-running,
overlapping edits (a refactor across `vehicle_manager.py`, a dependency bump).
For normal parallel feature work, ownership plus explicit-path commits is
lighter and has held.

## 15. Uninstalling

Coordination is additive and removable:

1. `py tools\agops.py admin pause` — instantly inert, nothing else changes.
2. Delete the four hook entries from `.claude/settings.json`.
3. Delete `.mcp.json`.
4. Delete `.agops/` and `tools/agops/`.

Nothing in the application depends on any of it.

## 16. Relationship to the old claim registry

`tools/claim.py` + `.claim/` (areas, resources, session handshake) came first and
is **superseded** by this. Its area definitions were carried over verbatim into
`.agops/project.json`, and `tools/guard.py`'s command parser is still used by the
new PreToolUse hook — it had three false-positive classes beaten out of it in
production (ASCII arrows read as redirects, heredoc bodies scanned as commands,
relative paths resolved against the wrong directory) and carries its own
regression suite. The old files remain on disk but are no longer wired to
anything; `LANES.md` remains the historical record and its decisions log is still
authoritative.

## 17. Tests

```
py tools\tests\test_agops.py
```

56 tests: registration, project isolation, task creation, **atomic claiming
under six real concurrent processes**, duplicate prevention, dependency chains,
file/area conflict severity, messaging and broadcast scoping, crash recovery
(including an assertion that recovery does not modify the working tree), agent
restart identity, ranked discovery, completion gates, resources, human override,
hook fail-open, and the MCP surface.

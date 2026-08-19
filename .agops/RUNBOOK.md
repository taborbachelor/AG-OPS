# AgOps runbook — how to actually run a work session

The short version: **one window watches, the rest work, and you dispatch.**
Full detail is in `README.md`; this is the procedure.

---

## 1. Open the board (once, first)

```
tools\monitor.cmd
```

Double-click it or run it. Leave it open for the whole session — second monitor,
side of the screen, wherever. It repaints in place every 2 seconds.

You now have one window that always answers "who is doing what, and did it ship".

## 2. Open your agent terminals

**This is the step that bites.** Claude Code binds hooks, slash commands and MCP
to the directory it was *launched* in, not one you `cd` to afterwards. Launch
from the repo or you get none of it:

```
cd C:\Users\jacks\rc-plane-app
claude
```

**The canary:** the first thing a correctly-launched session shows, before you
type anything, is

> You have joined the AgOps GCS engineering team as agent **alpha**.

No banner means the session is not on the team. Close it and relaunch from the
repo.

Open as many as you want. Each gets the next NATO name — alpha, bravo, charlie,
delta… They appear on the board within a second.

**Optional, worth it for a long session:** `claude --session-id <uuid>` makes an
agent's identity survive a restart, so closing and reopening that terminal gets
you the same name back rather than the next one.

## 3. Give each agent its specialty (optional, 5 seconds)

In each session:

```
/agops-join planner geometry
/agops-join ui frontend
/agops-join testing sitl
```

Specialties only affect ranking — they never stop an agent working anywhere. Skip
this if you already know who you want on what.

## 4. Dispatch the work

**Agents do not take tasks. You give them out.** From any session, or straight
from a terminal:

```
py tools\agops.py assign TASK-006 charlie --note "hold sitl-5760 while you test"
```

or in a session: `/agops-assign TASK-006 charlie`

That sets the owner, marks it IN_PROGRESS, and drops a message in that agent's
inbox — they pick it up on their next turn without you repeating yourself.

**If you'd rather just talk to a session directly**, that works too: tell it what
to do, and it claims with `--force` (which records that you authorised it). The
board stays accurate either way. That is the point — the board is not extra
process, it is the thing that stops two sessions on one task.

Watch the assign output: a **BLOCKING** conflict means the files overlap work
already in flight, and you want to know that before the agent starts typing.

## 5. While they work

The board is the whole interface. What to look for:

| You see | It means | Do |
|---|---|---|
| `IN PROGRESS` with a name and time | someone is on it | nothing |
| `!` or `STALE` next to a session | quiet 45m while holding work | `/agops-recover` in any session |
| `CONFLICTS` section appears | two agents near one file | tell one of them, or release a task |
| `COMPLETE … LOCAL ONLY` | committed here, nobody else can see it | push it |
| `COMPLETE … no commit recorded` | closed with nothing to show | ask that agent what happened |
| `OPEN` growing while sessions idle | you have work to hand out | assign it |

Agents talk to each other without you:

```
py tools\agops.py message bravo "waypoint alt is metres AGL now" --type WARNING
```

They see it on their next turn. You do not have to relay anything.

## 6. When an agent finishes

It runs `/agops-complete`, which makes it verify, review its own diff, commit
with an explicit pathspec, tell anyone affected, and record the task with the
commit hash. Then **it stops and waits** — it will recommend a next task if you
ask, but it will not take one.

To keep it going: say **continue**, or `/agops-continue TASK-007`.

Completion is refused outright if the agent reports failing tests, or if the
summary is not a real sentence. That gate is deliberate.

## 7. Ending a session

Just close the terminal. Session end marks the agent OFFLINE, frees any resource
locks it held, and **keeps its task ownership** — a closed terminal is not
evidence the work is finished. If it died mid-task, `/agops-recover` in another
session inspects and hands the work over without destroying anything.

Nothing to clean up. The board is right the next time you look.

---

## Things that go wrong, and the one-liners

```
py tools\agops.py doctor                       is coordination even working
py tools\agops.py admin release --task-id X    take a task back from anyone
py tools\agops.py admin assign X <agent>       hand it to someone else
py tools\agops.py admin clear-agents           wipe the roster, names restart at alpha
py tools\agops.py admin cancel --task-id X     kill a task
py tools\agops.py admin clear-locks            free every stuck resource
py tools\agops.py admin pause                  turn the whole system off, keep working
py tools\agops.py events --limit 40            who did what, when
```

**A slash command did nothing** — the session was launched outside the repo.
See step 2.

**An agent says it is blocked by a conflict** — that is the system working. Look
at the board, decide who gets the file, and release the other task.

**You want the agents to self-dispatch after all** —
`py tools\agops.py admin policy self_serve`. Reverse with `admin policy assigned`.

---

## Adding work

```
py tools\agops.py create "Fix the SITL teardown race" --priority HIGH ^
    --area AIR --file backend/tests/sitl/harness.py --depends-on TASK-006
```

`--depends-on` is the useful one: a task with unmet prerequisites sits BLOCKED
and cannot be dispatched until they complete, at which point it becomes OPEN on
its own. You never maintain that by hand.

Ground tasks in something real — a requirement, a failing test, a TODO, a
blocker you hit. Every invented task is one every agent reads past.

---

## The one habit that matters

**Stage explicit paths. Never `git add -A`.** Every agent shares one working
tree, so `-A` sweeps another session's half-finished work into your commit. The
agents are told this too, but it is worth knowing yourself — it is the single
hazard the coordination layer cannot catch for you.

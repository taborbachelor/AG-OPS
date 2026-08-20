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

Open as many as you want. They appear on the board within a second, named in
NATO order. **The first session of a fresh start — nobody live on the board —
recycles the roster and gets `alpha` again**; sessions arriving while others
are live take the next name. An offline agent that still owns a task keeps its
name through the recycle (same guard as `admin clear-agents`), so a crashed
session's work is never orphaned by a fresh morning.

**Still use `claude --session-id <uuid>` for a MID-SESSION relaunch.** Fresh
starts name themselves now, but reopening a terminal while the rest of the team
is live re-registers as a NEW agent unless you relaunch with the same uuid —
and stranding a task under a name nobody is using is the failure that prevents.

**Once running, each window tells you who it is.** The status line under the
prompt shows the agent's name, its current task, any resource locks it holds, and
unread messages in red — so you no longer scroll to the top to work out which
terminal is charlie. `OFF-BOARD` there means that session is live but holds no
task, which is the state rule 1 exists to prevent.

## 3. Give each agent its specialty (optional, 5 seconds)

In each session:

```
/agops-join planner geometry
/agops-join ui frontend
/agops-join testing sitl
```

Specialties only affect ranking — they never stop an agent working anywhere. Skip
this if you already know who you want on what.

## 2b. Worktrees for UI / PLANNER workers (pilot)

Launch those workers with their own tree:

```
cd C:\Users\jacks\rc-plane-app
claude --worktree ui-wave
```

Coordination still works — the tooling resolves the board to the MAIN
checkout through the worktree's `.git` pointer, so a worktree session joins
the same team, same tasks, same locks. What changes is everything the shared
tree made untrustworthy: no shared git index (a bare `git commit` can no
longer take a teammate's staged work), and a test run finally means *your*
change — not whatever four sessions happened to have on disk.

The costs, known and accepted: each worktree runs its own `npm install`
(~9 s), and **never junction `node_modules` or any shared directory into a
worktree** — deleting the worktree follows the junction and wipes the target
(it cost us the main checkout's `node_modules` on 2026-08-18). AIR stays in
the main checkout: SITL binaries and the `sitl-5760` lock live there, and its
scenarios need them. M5/M7 stay solo either way.

## 3b. Optional: a lead session, so you don't have to dispatch by hand

Open a fifth terminal from the repo and run `py tools\agops.py register --role
lead`, then follow **`.agops/MANAGER.md`** — the standing prompt, the watch
loop, the completion checks, and the brief it dispatches from all live there.
A lead never takes tasks and cannot edit the product (both enforced). With a
lead on duty, workers that finish a task wait briefly for their next dispatch
(`await-dispatch`) instead of going cold, so a whole wave runs without you
typing "continue" in each terminal. You talk to the lead; the lead talks to
the workers; everything judgment-shaped still escalates to you.

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
    --area AIR --file backend/tests/sitl/harness.py --depends-on TASK-006 ^
    --source "CLAUDE-CALEB.md RESUME HERE item 5"
```

`--source` is worth the extra ten seconds. A task with no stated source cannot be
checked for whether it is still real, and a queue of unverifiable tasks is one
everybody learns to scroll past.

`--depends-on` is the useful one: a task with unmet prerequisites sits BLOCKED
and cannot be dispatched until they complete, at which point it becomes OPEN on
its own. You never maintain that by hand.

Ground tasks in something real — a requirement, a failing test, a TODO, a
blocker you hit. Every invented task is one every agent reads past.

**Who creates them.** You do, or an agent does when it hits a genuine blocker
mid-work. Nothing generates tasks automatically, and nothing ever will: a queue
that fills itself is a queue nobody trusts. `created_by` on every task says who
put it there — check it if a task looks like it appeared from nowhere.

The eight tasks the board started with were seeded by Claude when the system was
built, drawn from `LANES.md` and `CLAUDE-CALEB.md`; each names its source.

---

## The one habit that matters

**Put the paths on the commit itself:**

```
git add <any new files>
git commit -F msg.txt -- path/one path/two
git show --stat HEAD          # check before you push
```

Never `git add -A` — every agent shares one working tree, so `-A` sweeps another
session's half-finished work into your commit. But staging your own paths is not
enough either, and this is the part that caught everyone including the person who
wrote the warning: **one working tree means one git index.** A bare `git commit`
takes whatever anyone else has staged, under your name and your message, with
nobody having broken the documented rule. Two agents were staged simultaneously
the night this was written; only the timing saved them.

`git commit -- <paths>` cannot name a file git has never seen, so new files still
need `git add <path>` first — adding a specific path is safe, it is the bare
commit that is not.

The PreToolUse guard now refuses a commit that would include a live agent's owned
file, so this is no longer purely an honour system. The habit still matters: the
guard only knows about paths a task actually lists.

## Before anyone says COMPLETE

```
py tools\seam_check.py          every route nothing calls
py tools\seam_check.py --ui     the sharper one: what no OPERATOR can reach
```

This repo has shipped a caller-less backend surface **four times** — the keepout
monitor running with zero rings, the post-flight scorecard, the turn-geometry
stats, and then rally points, which went to production dead. Each was complete,
tested, green and invisible. It is rule 4b for the agents; run it yourself before
you believe a wave is done.

`complete` also now requires `--commit <sha>`, or `--no-commit-reason "..."` when
the work genuinely produced none. COMPLETE with nothing to point at is a claim
nobody can check — TASK-005 sat that way for a day and nobody could say whether
the exe existed.

## Before a bench day

```
py tools\exe_status.py            is the packaged binary current, and from what
py tools\exe_status.py --stamp    run this immediately after every pyinstaller
```

`dist/` is gitignored, so the binary has no commit and no provenance of its own —
which is why TASK-005 sat COMPLETE for a day with nobody able to say what was in
it, and why the answer turned out to be bad: that binary predated `6c7a692` and
shipped powerline exclusion fences that were stored and never enforced. A bench
day on it would have flown with the operator's keepouts inert.

`backend/BUILD-PROVENANCE.json` is tracked, and the checker compares it against
git. **Stamping is not optional bookkeeping** — a rebuild without a stamp puts
the artifact straight back into the unknown state. Only `backend/app` and
`frontend/src` count as shipped code, so a docs commit never cries stale.

## Waiting on a lock

```
py tools\agops.py take sitl-5760 --queue     get in line
py tools\agops.py waiting                    who is in line, oldest first
```

`drop` messages whoever is next automatically, and skips anyone OFFLINE rather
than handing the lock to a closed terminal. Before this existed, an agent
finished its code, needed the SITL port, and idled behind a "ping me when you
drop it" agreement that only worked if the holder remembered.

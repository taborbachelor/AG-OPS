---
description: Join the AgOps engineering team as an agent and pick up work
---

You are joining the AgOps GCS engineering team. Do this now, in order.

1. `py tools\agops.py doctor` — if coordination is DEGRADED, say so plainly to
   the human before continuing. Never pretend the board is authoritative when it
   is not.
2. `py tools\agops.py whoami` — the SessionStart hook normally registered you
   already. If it says you are not registered, run:
   `py tools\agops.py register --specialty <area> --specialty <area>`
   Optional args: `--name <nato-name>` if the human named you, `--role lead`.
3. `py tools\agops.py status` — read the whole board. Note who is live, what
   they hold, and which files are occupied.
4. `py tools\agops.py inbox` — read anything addressed to you before you start.
5. `py tools\agops.py next` — the ranked queue for your specialties.

Then pick ONE task and claim it: `py tools\agops.py claim TASK-0XX`.

If the claim is refused because someone owns overlapping files, do not force it.
Pick a different task, or message the owner and agree first.

If no task fits — the queue is empty, everything conflicts, or the work needs a
decision only the human can make — say so and stop. Do not invent speculative
tasks to look busy.

$ARGUMENTS

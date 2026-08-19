---
description: Recover work from an agent whose session died
---

An agent went quiet while holding a task. Recover it WITHOUT destroying anything.

1. `py tools\agops.py recover` — dry run. It reports who is stale, what they
   hold, and the current git state. Nothing is changed.
2. Look at the actual work before deciding: `git status --short`, `git diff`,
   and the task record (`py tools\agops.py task TASK-0XX`). Their uncommitted
   changes are still in the tree and must be preserved.
3. If the agent is genuinely gone: `py tools\agops.py recover --apply`. This
   flags their task REVIEW + needs_recovery. It does not free it, delete
   anything, or touch git.
4. Only after you have inspected the state:
   `py tools\agops.py reclaim TASK-0XX --verified`

Never `git checkout`, `reset`, `stash` or `clean` another agent's work to
"clean up" before reclaiming. If their changes are broken, say so and let the
human decide.

$ARGUMENTS

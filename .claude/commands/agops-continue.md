---
description: Give the go-ahead — claim the next task and start working
---

This is the human's explicit go-ahead. You may now take work.

1. `py tools\agops.py next` — the ranked queue.
2. Pick the best fit. If `$ARGUMENTS` names a task (e.g. `TASK-005`), take that
   one instead; the human's choice always beats the ranking.
3. `py tools\agops.py claim TASK-0XX`
   - Refused for a file conflict? Do NOT force it. Say who owns the overlap and
     offer the next candidate instead.
   - Refused because someone beat you to it? Take the next one down the list.
4. Begin the work. Read the task body first (`py tools\agops.py task TASK-0XX`) —
   several carry notes about work already sitting in the tree.

When you finish, `/agops-complete` — which stops again and waits for the next
go-ahead rather than rolling straight into another task.

If nothing is claimable — queue empty, everything conflicts, or the next step
needs a decision only the human can make — say so plainly and stop. Do not
invent a task to have something to do.

$ARGUMENTS

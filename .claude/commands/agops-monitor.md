---
description: Full team board — sessions, task queue, and what actually shipped
---

Run `py tools\agops.py monitor` and show Tabor the output as-is. It is already
formatted for reading; do not re-summarise it into prose.

Then add a short read UNDER it — only what needs a decision or an eye:

* a session STALE while holding a task (recovery needed);
* a COMPLETE task marked **LOCAL ONLY** (committed here, nobody else can see it)
  or **no commit recorded** (finished with nothing to show for it);
* a BLOCKING conflict;
* work sitting OPEN with idle sessions available to take it;
* anything BLOCKED on a decision only he can make.

If none of that is true, say "nothing needs you" in one line and stop.

`--watch 10` repaints every ten seconds until Ctrl-C — use it only if he asks to
watch live, since it holds the session.

$ARGUMENTS

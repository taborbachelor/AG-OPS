---
description: Team board — sessions, tasks, and what actually shipped
---

**If Tabor wants a LIVE board, do not run this here.** A slash command cannot
hold a live view: a Claude session runs a command, gets its output, and returns.
Tell him to open a separate terminal and run one of:

```
tools\monitor.cmd                       (double-click works too)
py tools\agops.py monitor --watch       (same thing, 2s refresh)
py tools\agops.py monitor --watch 10    (slower, for a second screen)
```

That window repaints in place and can sit open all session. Ctrl-C stops it.

For a one-off snapshot right now, run `py tools\agops.py monitor` and show the
output as-is — it is already formatted; do not re-summarise it into prose.

Then add a short read UNDER it, covering only what needs a decision or an eye:

* a session STALE while holding a task (recovery needed);
* a COMPLETE task marked **LOCAL ONLY** (committed here, nobody else can see it)
  or **no commit recorded** (finished with nothing to show for it);
* a BLOCKING conflict;
* work sitting OPEN with idle sessions available to take it;
* anything BLOCKED on a decision only he can make.

If none of that is true, say "nothing needs you" in one line and stop.

$ARGUMENTS

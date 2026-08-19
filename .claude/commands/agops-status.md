---
description: Show the AgOps team board and interpret it
---

Run `py tools\agops.py status` and then tell the human what actually matters in
it, rather than repeating the table:

* is anyone STALE while holding a task (recovery needed)?
* are there BLOCKING conflicts right now?
* is the available queue empty, or is work sitting there unclaimed?
* is anything BLOCKED on a task nobody is working?
* did coordination report DEGRADED or PAUSED?

If nothing needs attention, say so in one line. Do not pad.

$ARGUMENTS

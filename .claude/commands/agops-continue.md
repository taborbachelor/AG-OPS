---
description: Tabor's go-ahead — take the named task and start working
---

This is an explicit instruction from Tabor. You may take work now.

1. If `$ARGUMENTS` names a task (`TASK-005`), that is the one. Otherwise run
   `py tools\agops.py next` and take the top clear candidate — but if the choice
   is not obvious, ask rather than guess.
2. `py tools\agops.py claim TASK-0XX --force`
   The `--force` is what records that a human authorised it: the default policy
   is `assigned`, so an unforced claim is refused by design.
   - Refused for a **file conflict**? Do NOT override it. Say who owns the
     overlap and stop — that is two agents about to edit one file, which is the
     exact thing this system exists to prevent.
3. `py tools\agops.py task TASK-0XX` — read the body first. Several tasks carry
   notes about work already sitting in the tree.
4. Do the work. `/agops-complete` when done, which reports and stops rather than
   rolling into another task.

$ARGUMENTS

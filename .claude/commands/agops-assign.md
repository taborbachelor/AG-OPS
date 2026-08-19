---
description: Dispatch a task to an agent (Tabor's control surface)
---

Tabor is dispatching work. `$ARGUMENTS` should name a task and an agent
(e.g. `TASK-005 charlie`); if either is missing, show the board and ask.

1. `py tools\agops.py status` — check the agent is live and the task is
   AVAILABLE, and look at the Conflicts section.
2. `py tools\agops.py assign <TASK-ID> <agent> --note "<anything they should
   know first>"`

This sets the owner, marks it IN_PROGRESS, and messages the agent — they pick it
up from their inbox on their next turn. Report back any conflict the assignment
surfaced; a BLOCKING one means the assigned files overlap work already in
flight, and Tabor should know before that agent starts typing.

$ARGUMENTS

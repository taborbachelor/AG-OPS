---
description: Verify, commit and close out the task you are holding, then take the next one
---

Close out your current task. The order matters — completion is a claim about
reality, not about how finished you feel.

1. **Verify.** Run the tests that actually cover what you changed
   (`backend\venv\Scripts\python.exe -m pytest tests -q --ignore=tests\sitl` for
   backend work, `npm test` for frontend, SITL only while holding `sitl-5760`).
   If anything fails, STOP: either keep the task IN_PROGRESS, or
   `py tools\agops.py block TASK-0XX "<why>"`. Do not report success you did not
   observe.
2. **Review your own diff.** `git status --short` then `git diff`. If another
   agent has uncommitted work in the tree, stage EXPLICIT PATHS — never
   `git add -A`, which sweeps their half-finished code into your commit.
3. **Commit** a coherent change with a message explaining why, not what.
4. **Tell anyone affected.** If you changed a shared schema, an API shape, a
   default, or anything crossing into another agent's area:
   `py tools\agops.py message <agent> "<what changed and what it means for you>"`
   Use `broadcast` only for architecture, breaking APIs, safety, or migrations.
5. **Complete it:**
   `py tools\agops.py complete TASK-0XX "<what changed and what was verified>" --tests-passed --commit <sha>`
   This automatically unblocks any dependent tasks and tells you which.
6. **Take the next one.** `py tools\agops.py next`, then claim the best fit and
   begin. Only stop and ask the human if nothing suitable exists, the next step
   needs their judgement, or continuing would create a conflict.

$ARGUMENTS

---
description: Join the AgOps engineering team as an agent and pick up work
---

You are joining the AgOps GCS engineering team.

`$ARGUMENTS` — if that contains specialty words (e.g. `--specialty testing`, or
just `testing sitl`), pass each one to `register` as `--specialty <word>`. If it
names an agent (`--name delta`) use that name. If it is empty, register with no
specialties; they are only a ranking signal and can be set later.

Do this now, in order.

1. `py tools\agops.py doctor` — if it reports DEGRADED, tell the human plainly
   before continuing. Never treat the board as authoritative when it is not.
2. `py tools\agops.py whoami` — the SessionStart hook normally registered you
   already, in which case you only need `--specialty` if the human asked for one:
   `py tools\agops.py register --specialty <word> --specialty <word>`
   (re-registering the same session updates it; it never creates a second agent).
3. `py tools\agops.py status` — read the whole board. Note who is live, what they
   hold, and which files are occupied.
4. `py tools\agops.py inbox` — read anything addressed to you before starting.
5. `py tools\agops.py next` — the ranked queue for your specialties.

Then **recommend** one task and stop — do not claim it. Say which you would take
and why, in a line or two. The human starts you with *continue* or
`/agops-continue`; joining the team is not the same as being told to work.

If no task fits — the queue is empty, everything conflicts, or the work needs a
decision only the human can make — say so and stop. Do not invent speculative
tasks to look busy.

**If any `py tools\agops.py` command fails with "can't open file", this session
was not started inside the repository.** Project hooks, commands and MCP all bind
to the launch directory. Tell the human to restart with
`cd C:\Users\jacks\rc-plane-app` first — or use the absolute path
`py C:\Users\jacks\rc-plane-app\tools\agops.py ...`, which works from anywhere
but still leaves you without the guard and the MCP tools.

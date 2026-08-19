---
description: Hand your task to another agent with everything they need
---

Hand off cleanly. A handoff that omits the problems is worse than no handoff,
because the receiver starts by trusting it.

Gather honestly, then run:

```
py tools\agops.py handoff TASK-0XX <agent> ^
  --state "<what works and what does not>" ^
  --changed "<what you actually changed>" ^
  --remaining "<what is left, concretely>" ^
  --problems "<known bugs, dead ends, suspicions>" ^
  --file <path> --file <path> ^
  --tests "<what you ran and what the result was>" ^
  --next "<where you would start>"
```

Ownership transfers with the message. Before you run it: commit or clearly
describe any uncommitted work, because the receiver inherits the working tree as
it stands.

$ARGUMENTS

#!/usr/bin/env python
"""SessionEnd hook: mark the agent offline WITHOUT releasing its work.

The asymmetry is deliberate. A closing terminal is not evidence that the work is
finished or abandoned -- it is equally likely to be a crash mid-edit. So:

  * agent status becomes OFFLINE (it will stop appearing as live, and stops
    blocking anyone's edits);
  * ephemeral resource locks it held ARE released, because those are pure
    mutual exclusion (the SITL port, the exe build) and holding them from a dead
    session helps nobody;
  * task ownership is PRESERVED. Reclaiming it requires the explicit recovery
    path, where somebody looks at the git state first.

That last point is the difference between "the coordinator freed a task" and
"the coordinator handed half-finished work to an agent who did not know it was
half-finished".
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    from agops import core

    session = payload.get("session_id") or ""
    conn = core.connect()
    try:
        r = conn.execute("SELECT * FROM agents WHERE agent_id=? OR session_id=?",
                         (session, session)).fetchone()
        if r is None:
            return 0
        name = r["name"]
        conn.execute("UPDATE resources SET holder=NULL WHERE holder=?", (name,))
    finally:
        conn.close()

    res = core.unregister_agent(name)
    if res.get("tasks_still_owned"):
        core.send_message(
            name, "ALL",
            "%s went offline still holding %s. Work and git state are preserved. "
            "Do not take it over blind: py tools\\agops.py recover --apply, then "
            "reclaim --verified after checking." % (name, ", ".join(res["tasks_still_owned"])),
            msg_type="WARNING")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)

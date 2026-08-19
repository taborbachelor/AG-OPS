#!/usr/bin/env python
"""Stop hook: heartbeat, deliver messages, and surface the next task.

Runs when the agent finishes a turn. Three jobs, all cheap:

1.  **Heartbeat.** Liveness must not depend only on the guard firing -- during
    the first three-session run, sessions whose PreToolUse hook never loaded had
    their claims silently decay while they were actively working. Several
    independent paths now refresh it: this hook, the guard, and every CLI call.
2.  **Deliver mail.** A teammate's message is worthless if nobody reads it. Any
    unread message is injected here, at the natural moment the agent is between
    pieces of work.
3.  **Point at the next task.** An idle agent with a queue in front of it should
    not be asking the human what to do. This surfaces the ranked candidates; the
    agent decides and claims.

Never blocks, never fails loudly.
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

    if not core.load_config().get("coordination_enabled", True):
        return 0

    session = payload.get("session_id") or ""
    conn = core.connect()
    try:
        r = conn.execute("SELECT * FROM agents WHERE agent_id=? OR session_id=?",
                         (session, session)).fetchone()
        if r is None:
            return 0
        me, cur_task = r["name"], r["current_task"]
        conn.execute("UPDATE agents SET last_heartbeat=? WHERE name=?",
                     (core._now(), me))
    finally:
        conn.close()

    lines = []
    unread = core.inbox(me, unread_only=True, mark_read=True)
    for m in reversed(unread):
        lines.append("MESSAGE [%s] from %s%s: %s"
                     % (m["msg_type"], m["sender"],
                        (" re %s" % m["related_task"]) if m["related_task"] else "",
                        m["content"]))

    if not cur_task:
        nxt = core.next_tasks(agent=me, limit=3)
        if nxt:
            lines.append("You hold no task. Ranked available work:")
            for t in nxt:
                lines.append("  %s  [%s]  %s%s"
                             % (t["task_id"], t["priority"], t["title"][:50],
                                "" if t["_conflict"] == "NONE"
                                else "  (conflict: %s)" % t["_conflict"]))
            lines.append("Claim one with: py tools\\agops.py claim <TASK-ID>  "
                         "-- or tell the human why none of these is the right "
                         "next move.")

    if not lines:
        return 0
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "Stop",
        "additionalContext": "\n".join(lines),
    }}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)

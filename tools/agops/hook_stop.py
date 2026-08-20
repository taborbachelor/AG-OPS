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
3.  **Point at the next task -- without taking it.** An idle agent should know
    what is available without the human having to look it up. Whether it may
    ACT on that is the `auto_claim` policy, and it is off by default: proposing
    costs a sentence, claiming commits an agent, a file lock and a commit. A
    session asked for a status report once claimed a task and shipped a feature,
    which is the behaviour this default exists to prevent.

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
        me, cur_task, role = r["name"], r["current_task"], (r["role"] or "worker")
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

    # A dispatch must REACH a session, not sit in its inbox: an unread DISPATCH
    # blocks the stop so the agent proceeds with the assigned task now. The
    # message was just marked read above, so this fires exactly once per
    # dispatch -- an agent that then genuinely gets stuck can still stop.
    if any(m["msg_type"] == "DISPATCH" for m in unread):
        lines.append(
            "You have been DISPATCHED (see the message above). Do not stop: "
            "read the task (py tools\\agops.py task <id>) and proceed with it "
            "now, under the normal rules. If something genuinely prevents "
            "starting, say what and message the sender.")
        print(json.dumps({"decision": "block", "reason": "\n".join(lines)}))
        return 0

    # Fresh off a completion with a lead on duty and work that could arrive:
    # wait for the next dispatch instead of going cold. Every scope condition
    # lives in maybe_enter_standby; this hook just relays its verdict.
    if not cur_task and role != "lead":
        sb = core.maybe_enter_standby(me)
        if sb.get("standby"):
            lines.append(
                "The lead is on duty and more work may be coming. Do not stop "
                "yet: run  py tools\\agops.py await-dispatch --timeout %d  "
                "(standby %d/%d; run it with a tool timeout above %d s). If it "
                "returns a DISPATCH, proceed with that task. If it returns no "
                "dispatch, say you are standing down and end your turn."
                % (sb["wait_s"], sb["cycle"], sb["cycles"], sb["wait_s"]))
            print(json.dumps({"decision": "block", "reason": "\n".join(lines)}))
            return 0

    if not cur_task and role != "lead":
        nxt = core.next_tasks(agent=me, limit=3)
        if nxt:
            auto = core.load_config().get("auto_claim", False)
            lines.append("You hold no task. Ranked available work:")
            for t in nxt:
                lines.append("  %s  [%s]  %s%s"
                             % (t["task_id"], t["priority"], t["title"][:50],
                                "" if t["_conflict"] == "NONE"
                                else "  (conflict: %s)" % t["_conflict"]))
            policy = core.load_config().get("claim_policy", "assigned")
            if policy == "assigned":
                lines.append(
                    "You hold nothing, and work here is DISPATCHED, not taken. "
                    "Do not claim. If the human asks what is next, recommend one "
                    "of the above in a line or two and stop. They assign it "
                    "(py tools\\agops.py assign <TASK-ID> %s) or tell you to "
                    "start." % me)
            elif auto:
                lines.append("auto_claim is ON: claim the best fit with "
                             "py tools\\agops.py claim <TASK-ID> and begin.")
            else:
                lines.append(
                    "DO NOT CLAIM ANY OF THESE YET. Tell the human which one you "
                    "would take and why, in one or two lines, then STOP and wait. "
                    "Claim and start only when they say continue (or name a task). "
                    "This applies however obvious the next step looks.")

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

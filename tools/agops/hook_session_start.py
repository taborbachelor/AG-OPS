#!/usr/bin/env python
"""SessionStart hook: join the team, then say what is going on.

This is the whole "frictionless onboarding" requirement in one file. A new
Claude Code session lands in this repo and, without the human doing anything:

  * learns the project identity and its OWN session_id (identity before
    ownership -- claiming under a guessed id is what locks an agent out of its
    own files);
  * is registered as an agent with a deterministic NATO name;
  * sees the rest of the team, their status, and what they are holding;
  * sees ranked available work and any unread messages;
  * gets the exact commands for claiming and communicating.

Fails open and silent. A session must start even if coordination is broken --
but if it IS broken, say so rather than pretending it worked.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def build_context(payload):
    from agops import core
    from agops.cli import render_status, _hms

    session = payload.get("session_id") or ""
    cwd = payload.get("cwd") or os.getcwd()
    cfg = core.load_config()

    reg = core.register_agent(session_id=session or None, cwd=cwd, pid_=os.getpid())
    me = reg["agent"]
    name = me["name"]

    st = core.project_status()
    others = [a for a in st["agents"]
              if a["name"] != name and a["status"] != "OFFLINE"]
    nxt = core.next_tasks(agent=name, limit=5)
    unread = core.inbox(name, unread_only=True, mark_read=False)

    L = []
    L.append("You have joined the %s engineering team as agent **%s**."
             % (cfg.get("project_name", "AgOps"), name))
    L.append("project_id: %s   |   your session_id: %s"
             % (cfg.get("project_id"), session or "(not supplied)"))
    if not cfg.get("coordination_enabled", True):
        L.append("")
        L.append("!! COORDINATION IS PAUSED by human override. Claims and the "
                 "conflict guard are inert. Work normally and coordinate by hand.")
    L.append("")
    L.append("TEAM RIGHT NOW")
    if others:
        for a in others:
            L.append("  %-9s %-9s %-10s %s%s"
                     % (a["name"], a["status"], a["current_task"] or "-",
                        ",".join(a["specialties"][:3]),
                        "   STALE %s quiet" % _hms(a["quiet_s"]) if a["stale"] else ""))
    else:
        L.append("  nobody else is active -- you are working solo, guard is inert")

    busy = [(c["path"], c["owner"]) for c in st["conflicts"]]
    owned_paths = []
    for t in st["tasks"]["IN_PROGRESS"]:
        for f in t["affected_files"]:
            owned_paths.append("  %-46s %s (%s)" % (f, t["owner"], t["task_id"]))
    if owned_paths:
        L.append("")
        L.append("FILES CURRENTLY OWNED BY SOMEONE ELSE")
        L.extend(owned_paths[:12])

    L.append("")
    auto = cfg.get("auto_claim", False)
    L.append("AVAILABLE WORK (ranked for you)"
             if auto else
             "AVAILABLE WORK (ranked for you) -- DO NOT CLAIM WITHOUT BEING ASKED")
    if nxt:
        for t in nxt:
            L.append("  %-10s %-8s %-9s %s"
                     % (t["task_id"], t["priority"],
                        "conflict:" + t["_conflict"] if t["_conflict"] != "NONE" else "clear",
                        t["title"][:44]))
    else:
        L.append("  none queued -- ask the human, or create tasks from real "
                 "requirements (never speculative ones)")

    if unread:
        L.append("")
        L.append("UNREAD MESSAGES: %d  (read with: py tools\\agops.py inbox)" % len(unread))
        for m in unread[:3]:
            L.append("  [%s] from %s: %s" % (m["msg_type"], m["sender"],
                                             m["content"][:70]))

    L.append("")
    if not auto:
        L.append("HOW WORK STARTS HERE: you do NOT pick up tasks on your own. "
                 "Answer whatever the human asked. If they ask what is next, "
                 "recommend one of the above and wait. Claim and begin only when "
                 "they say continue, or name a task. Starting work unasked costs "
                 "them a commit and a file lock they did not agree to.")
        L.append("")
    L.append("HOW TO WORK HERE (you are %s)" % name)
    L.append("  py tools\\agops.py status                     the whole board")
    L.append("  py tools\\agops.py next                       ranked work for you")
    L.append("  py tools\\agops.py claim TASK-00X             atomic; exactly one winner")
    L.append("  py tools\\agops.py conflicts <files...>       before editing anything new")
    L.append("  py tools\\agops.py message <agent> \"...\"      talk to a teammate")
    L.append("  py tools\\agops.py complete TASK-00X \"...\" --tests-passed --commit <sha>")
    L.append("")
    L.append("RULES THAT ARE NOT OPTIONAL: claim before you implement; never edit "
             "a file another agent's IN_PROGRESS task owns; never mark COMPLETE "
             "with failing tests; tell teammates about changes that cross into "
             "their area. Full detail: CLAUDE.md and .agops/README.md.")
    return "\n".join(L)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    try:
        context = build_context(payload)
    except Exception as exc:
        context = ("AgOps GCS repo. **Coordination layer is UNAVAILABLE** (%s). "
                   "This is not fatal: work normally, but claims are not being "
                   "enforced, so coordinate by hand and tell the human. "
                   "Diagnose with: py tools\\agops.py doctor" % exc)
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": context,
    }}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)

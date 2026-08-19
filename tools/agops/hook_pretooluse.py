#!/usr/bin/env python
"""PreToolUse hook: stop an edit that lands in another live agent's files.

Scope discipline, learned the expensive way. This hook blocks ONE thing: a write
to a path that a *currently live* agent's *IN_PROGRESS* task explicitly lists as
an affected file. That is a precise fact from the task record, not a heuristic
about who might care about what.

Everything else is allowed and, at most, mentioned:
  * area-level overlap is advisory (a WARNING in the transcript, never a block);
  * reads across areas are always fine and constantly useful;
  * a stale or offline agent's files do not block anybody;
  * solo work is never guarded -- with fewer than two live agents there is
    nobody to collide with.

The command-parsing half (which paths is this shell command about to write?) is
deliberately reused from tools/guard.py rather than reimplemented. That parser
already had three false-positive classes beaten out of it in production -- ASCII
arrows read as redirects, heredoc bodies scanned as commands, relative paths
resolved against the wrong directory -- and it carries its own regression suite
in tools/test_guard.py. Rewriting it would mean re-earning those fixes.

Fails OPEN on any internal error, and heartbeats the acting agent on the way
through so liveness costs nothing.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
REPO = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)


def _load_guard():
    """Borrow the proven target extraction from the legacy guard, if present."""
    import importlib.util
    path = os.path.join(TOOLS, "guard.py")
    if not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location("_legacy_guard", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _targets(tool, ti):
    if tool in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
        p = ti.get("file_path") or ti.get("notebook_path")
        return [p] if p else []
    g = _load_guard()
    if g is not None and hasattr(g, "_targets"):
        return g._targets(tool, ti)
    return []


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    from agops import core

    cfg = core.load_config()
    if not cfg.get("coordination_enabled", True):
        return 0
    mode = cfg.get("enforcement", "advisory")
    if mode == "off":
        return 0

    session = payload.get("session_id") or ""
    tool = payload.get("tool_name") or ""
    ti = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or REPO

    # Who is acting, and is anybody else even here?
    conn = core.connect()
    try:
        r = conn.execute("SELECT name FROM agents WHERE agent_id=? OR session_id=?",
                         (session, session)).fetchone()
        me = r["name"] if r else None
        if me:
            conn.execute("UPDATE agents SET last_heartbeat=? WHERE name=?",
                         (core._now(), me))
    finally:
        conn.close()

    live = [a for a in core.list_agents(include_offline=False) if not a["stale"]]
    if len(live) < 2:
        return 0                       # solo: nothing to collide with

    targets = _targets(tool, ti)
    if not targets:
        return 0

    rel = []
    for t in targets:
        p = t if os.path.isabs(t) else os.path.join(cwd, t)
        p = os.path.abspath(p)
        try:
            if os.path.commonpath([p, REPO]) != REPO:
                continue               # outside the repo is none of our business
        except ValueError:
            continue
        rel.append(os.path.relpath(p, REPO).replace("\\", "/"))
    if not rel:
        return 0

    for pattern in cfg.get("always_open", []):
        rel = [p for p in rel if not core._match(p, pattern)]
    if not rel:
        return 0

    res = core.check_conflicts(rel, agent=me)
    if res["level"] == "NONE":
        return 0

    blocking = [c for c in res["conflicts"] if c["level"] == "BLOCKING"]
    if blocking and mode in ("advisory", "blocking"):
        c = blocking[0]
        sys.stderr.write(
            "BLOCKED by AgOps: %s is owned by agent %s under %s (%s).\n"
            "Another agent is actively editing that file right now.\n"
            "  talk to them:  py tools\\agops.py message %s \"...\" --task %s\n"
            "  see the board: py tools\\agops.py status\n"
            "  if you have agreed to share it, they release or hand off:\n"
            "                 py tools\\agops.py handoff %s <you> --state ... \n"
            % (c["path"], c["owner"], c["task_id"], c["title"],
               c["owner"], c["task_id"], c["task_id"]))
        return 2

    warn = [c for c in res["conflicts"] if c["level"] == "WARNING"]
    if warn:
        c = warn[0]
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext":
                "AgOps advisory: %s sits in area %s, which %s is working in "
                "(%s). Not blocked -- but if this turns into a shared surface, "
                "tell them." % (c["path"], c["their_path"], c["owner"], c["task_id"]),
        }}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)                    # rule 2: never brick an edit

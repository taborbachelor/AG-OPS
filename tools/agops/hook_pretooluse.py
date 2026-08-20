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


def _commit_paths(command, cwd):
    """What a `git commit` in this command is about to commit -- or None.

    One working tree means one git index. "Stage explicit paths, never
    git add -A" stops you sweeping another agent's UNSTAGED work; it does
    nothing about their STAGED work. Two agents staged simultaneously on the
    evening this was written, and either committing would have taken the
    other's files under their own name and message, having broken no documented
    rule.

    `git commit -- path...` commits exactly those paths and ignores the rest of
    the index, so when a pathspec is present those are the paths at risk.
    Otherwise everything staged is.

    Deliberately conservative: a commit message containing " -- " is misread as
    a pathspec, which makes this check skip rather than fire. Every ambiguity
    resolves toward allowing the commit -- a guard that blocks legitimate work
    trains people to route around the mechanism.
    """
    import subprocess

    if not command or "git" not in command or "commit" not in command:
        return None
    # Crude but sufficient: some form of `git ... commit`, not `git log commit`.
    if not any(seg.strip().startswith("git ") and " commit" in seg
               for seg in command.replace("\n", ";").split(";")):
        return None

    if " -- " in command:
        tail = command.split(" -- ", 1)[1]
        paths = [p.strip().strip("'\"") for p in tail.split() if not p.startswith("-")]
        return [p.replace("\\", "/") for p in paths if p] or None

    try:
        out = subprocess.run(["git", "-C", cwd, "diff", "--cached", "--name-only"],
                             capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return [p.strip().replace("\\", "/") for p in out.stdout.splitlines() if p.strip()]


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
        r = conn.execute("SELECT name, role FROM agents WHERE agent_id=? "
                         "OR session_id=?", (session, session)).fetchone()
        me = r["name"] if r else None
        role = (r["role"] or "worker") if r else "worker"
        if me:
            conn.execute("UPDATE agents SET last_heartbeat=? WHERE name=?",
                         (core._now(), me))
            # Record WHAT is being edited, not just that a heartbeat happened:
            # the board's "WORKING, 12m quiet" answers nothing; "editing
            # SprayPanel.jsx, 2m ago" answers whether the agent is stuck.
            if tool in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
                raw = ti.get("file_path") or ti.get("notebook_path")
                if raw:
                    p = raw if os.path.isabs(raw) else os.path.join(cwd, raw)
                    p = os.path.abspath(p)
                    try:
                        if os.path.commonpath([p, REPO]) == REPO:
                            conn.execute(
                                "UPDATE agents SET note=? WHERE name=?",
                                ("editing " + os.path.relpath(p, REPO)
                                 .replace("\\", "/"), me))
                    except ValueError:
                        pass
    finally:
        conn.close()

    # The lead coordinates; it does not edit the product. Checked before the
    # solo shortcut below, because a lead alone on the board is still a lead.
    # Structured writes only -- shell stays best-effort advisory, per the
    # field-notes lesson that parsing commands blocks legitimate work.
    if role == "lead" and tool in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
        raw = ti.get("file_path") or ti.get("notebook_path") or ""
        p = raw if os.path.isabs(raw) else os.path.join(cwd, raw)
        p = os.path.abspath(p)
        inside = False
        try:
            inside = os.path.commonpath([p, REPO]) == REPO
        except ValueError:
            pass
        if inside:
            rel_p = os.path.relpath(p, REPO).replace("\\", "/")
            allowed = cfg.get("lead_writable", [".agops/*", ".agops/**"])
            if not any(core._match(rel_p, pat) for pat in allowed):
                sys.stderr.write(
                    "BLOCKED by AgOps: you are the lead -- you coordinate, "
                    "verify and dispatch; you do not edit the product (%s).\n"
                    "If a file needs changing, dispatch it or message the "
                    "owner. Genuine tooling/doc work belongs to a worker "
                    "session or the human.\n" % rel_p)
                return 2

    live = [a for a in core.list_agents(include_offline=False) if not a["stale"]]
    if len(live) < 2:
        return 0                       # solo: nothing to collide with

    def owned_by_others(paths):
        """Trim always_open, then ask the same ownership question as an edit."""
        keep = list(paths)
        for pattern in cfg.get("always_open", []):
            keep = [p for p in keep if not core._match(p, pattern)]
        if not keep:
            return []
        found = core.check_conflicts(keep, agent=me)
        return [c for c in found["conflicts"] if c["level"] == "BLOCKING"]

    if tool in ("Bash", "PowerShell"):
        at_risk = _commit_paths(ti.get("command") or "", cwd)
        if at_risk:
            theirs = owned_by_others(at_risk)
            if theirs and mode in ("advisory", "blocking"):
                c = theirs[0]
                sys.stderr.write(
                    "BLOCKED by AgOps: this commit would include %s, which %s "
                    "owns under %s.\n"
                    "One working tree means ONE git index -- staging only your "
                    "own paths does not stop a bare `git commit` taking whatever "
                    "else is staged, under your name and your message.\n"
                    "  commit only yours:  git commit -F msg.txt -- path/one "
                    "path/two\n"
                    "  (new files need `git add <path>` first -- a pathspec "
                    "cannot name a file git has never seen)\n"
                    "  then verify:        git show --stat HEAD\n"
                    "If you have genuinely agreed to commit their work, say so "
                    "to them first: py tools\\agops.py message %s \"...\"\n"
                    % (c["path"], c["owner"], c["task_id"], c["owner"]))
                return 2

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

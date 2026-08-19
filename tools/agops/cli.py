#!/usr/bin/env python
"""AgOps coordination CLI.

    py tools\\agops.py status                  the dashboard
    py tools\\agops.py whoami                  who am I in this session
    py tools\\agops.py next                    what should I work on
    py tools\\agops.py claim TASK-007          take it (atomically)
    py tools\\agops.py complete TASK-007 ...   finish it

Every command also accepts --json for machine consumption. The CLI is the
fallback path: if the MCP server is unavailable for any reason, everything here
still works, which is what keeps a broken coordinator from stopping work.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agops import core  # noqa: E402
from agops.core import AgopsError  # noqa: E402


# --- identity ----------------------------------------------------------------

def current_agent(explicit=None):
    """Resolve who is running this command.

    Order: explicit --agent, then AGOPS_AGENT, then the recorded session id, then
    a single live agent if there is exactly one. Identity is never guessed when
    it is ambiguous -- claiming under the wrong name is the failure that locks an
    agent out of its own files.
    """
    if explicit:
        return explicit
    env = os.environ.get("AGOPS_AGENT")
    if env:
        return env
    sid = os.environ.get("CLAUDE_SESSION_ID")
    if sid:
        conn = core.connect()
        try:
            r = conn.execute("SELECT name FROM agents WHERE agent_id=? OR session_id=?",
                             (sid, sid)).fetchone()
            if r:
                return r["name"]
        finally:
            conn.close()
    live = [a for a in core.list_agents(include_offline=False) if not a["stale"]]
    if len(live) == 1:
        return live[0]["name"]
    return None


def need_agent(args):
    who = current_agent(getattr(args, "agent", None))
    if not who:
        raise AgopsError(
            "cannot tell which agent you are. Pass --agent <name>, or set "
            "AGOPS_AGENT, or run `py tools\\agops.py register` first.")
    return who


# --- rendering ---------------------------------------------------------------

def _hms(sec):
    sec = int(sec or 0)
    if sec < 90:
        return "%ds" % sec
    if sec < 5400:
        return "%dm" % (sec // 60)
    return "%dh%02dm" % (sec // 3600, (sec % 3600) // 60)


def render_status(st) -> str:
    L = []
    head = "%s TEAM" % st["project_name"].upper()
    L.append(head)
    if not st["coordination_enabled"]:
        L.append("!! coordination is PAUSED -- claims and guards are inert")
    L.append("")
    L.append("Agents")
    L.append("-" * 62)
    if not st["agents"]:
        L.append("  (nobody registered)")
    for a in st["agents"]:
        flag = ""
        if a["status"] == "OFFLINE":
            flag = "  offline"
        elif a["stale"]:
            flag = "  STALE %s quiet" % _hms(a["quiet_s"])
        spec = (",".join(a["specialties"][:3])) if a["specialties"] else ""
        L.append("  %-10s %-10s %-10s %-22s%s"
                 % (a["name"], a["status"], a["current_task"] or "-", spec, flag))
    for label, key in (("Available Tasks", "AVAILABLE"),
                       ("In Progress", "IN_PROGRESS"),
                       ("Blocked", "BLOCKED"),
                       ("Needs Review / Recovery", "REVIEW")):
        rows = st["tasks"].get(key) or []
        L.append("")
        L.append(label)
        L.append("-" * 62)
        if not rows:
            L.append("  none")
        for t in rows:
            extra = ""
            if key == "BLOCKED":
                extra = "  <- %s" % (t["blocked_reason"] or "?")
            elif key == "IN_PROGRESS":
                extra = "  [%s]" % (t["owner"] or "?")
            elif key == "REVIEW" and t["needs_recovery"]:
                extra = "  RECOVERY: %s" % (t["recovery_note"] or "")
            L.append("  %-10s %-8s %s%s" % (t["task_id"], t["priority"],
                                            t["title"][:34], extra))
    L.append("")
    L.append("Conflicts")
    L.append("-" * 62)
    if not st["conflicts"]:
        L.append("  none")
    for c in st["conflicts"]:
        L.append("  %-8s %s  (%s)" % (c["level"], c["path"], c["why"]))
    if st["resources"]:
        L.append("")
        L.append("Held resources")
        L.append("-" * 62)
        for r in st["resources"]:
            L.append("  %-14s %s" % (r["name"], r["holder"]))
    L.append("")
    L.append("Recent Activity")
    L.append("-" * 62)
    for e in st["recent_events"][:8]:
        L.append("  %-10s %-22s %s" % (e["actor"] or "-", e["kind"],
                                       (e["subject"] or "")[:28]))
    return "\n".join(L)


# --- monitor -----------------------------------------------------------------

_W = 78


def _rule(ch="-"):
    return " " + ch * (_W - 2)


def _clock(ts):
    if not ts:
        return "--:--"
    return time.strftime("%H:%M", time.localtime(ts))


def _ago(ts):
    return _hms(time.time() - ts) if ts else "-"


def _wrap(text, width):
    # ASCII only: a unicode ellipsis renders as a replacement glyph in the
    # Windows console this gets read in, which makes a tidy dashboard look
    # broken.
    return text if len(text) <= width else text[:width - 2] + ".."


def render_monitor(st, commit_state=None) -> str:
    """The at-a-glance board: who is here, what is open, what shipped.

    Grouped by what a human actually scans for, in that order: who is live, what
    is being worked right now, what is free to hand out, what is stuck, and what
    landed. Completion carries the commit AND whether it is pushed, because
    those come apart and only one of them means other people can see the work.
    """
    commit_state = commit_state or {}
    agents = st["agents"]
    live = [a for a in agents if a["status"] != "OFFLINE" and not a["stale"]]
    tasks = st["tasks"]
    L = []
    L.append("=" * _W)
    head = " %s" % st["project_name"].upper()
    L.append(head + time.strftime("%Y-%m-%d %H:%M").rjust(_W - len(head) - 1))
    L.append("=" * _W)
    if not st["coordination_enabled"]:
        L.append(" !! COORDINATION PAUSED - claims and the guard are inert")
    L.append("")

    # --- sessions -----------------------------------------------------------
    L.append(" SESSIONS  (%d live, %d offline, %d stale)"
             % (len(live),
                sum(1 for a in agents if a["status"] == "OFFLINE"),
                sum(1 for a in agents if a["stale"] and a["status"] != "OFFLINE")))
    L.append(_rule())
    if not agents:
        L.append("   nobody registered - open Claude Code in this repo to join")
    for a in agents:
        dot = "*" if a in live else ("!" if a["stale"] else "-")
        task = a["current_task"] or "--"
        note = ""
        if a["status"] == "OFFLINE":
            note = "offline %s" % _ago(a["last_heartbeat"])
        elif a["stale"]:
            note = "STALE %s quiet" % _ago(a["last_heartbeat"])
        else:
            note = "%s quiet" % _ago(a["last_heartbeat"])
        L.append("  %s %-9s %-9s %-10s %-22s %s"
                 % (dot, a["name"], a["status"], task,
                    _wrap(",".join(a["specialties"]), 22), note))
    L.append("")

    # --- in progress --------------------------------------------------------
    ip = tasks.get("IN_PROGRESS") or []
    L.append(" IN PROGRESS  (%d)" % len(ip))
    L.append(_rule())
    if not ip:
        L.append("   nothing being worked")
    for t in ip:
        L.append("  %-10s %-8s %s" % (t["task_id"], t["priority"],
                                      _wrap(t["title"], 46)))
        L.append("  %-10s taken by %s at %s  (%s ago)"
                 % ("", t["owner"] or "?", _clock(t["claimed_at"]),
                    _ago(t["claimed_at"])))
    L.append("")

    # --- open ---------------------------------------------------------------
    av = tasks.get("AVAILABLE") or []
    L.append(" OPEN  (%d)   assign with: py tools\\agops.py assign <TASK> <agent>"
             % len(av))
    L.append(_rule())
    if not av:
        L.append("   queue empty")
    for t in av:
        files = ", ".join(t["affected_files"][:2])
        L.append("  %-10s %-8s %-38s %s"
                 % (t["task_id"], t["priority"], _wrap(t["title"], 38),
                    _wrap(files, 17)))
    L.append("")

    # --- blocked ------------------------------------------------------------
    bl = tasks.get("BLOCKED") or []
    rv = tasks.get("REVIEW") or []
    if bl or rv:
        L.append(" BLOCKED / NEEDS REVIEW  (%d)" % (len(bl) + len(rv)))
        L.append(_rule())
        for t in bl:
            L.append("  %-10s %-8s %-29s waiting: %s"
                     % (t["task_id"], t["priority"], _wrap(t["title"], 29),
                        _wrap(t["blocked_reason"] or "?", 22)))
        for t in rv:
            L.append("  %-10s %-8s %-29s RECOVERY (%s)"
                     % (t["task_id"], t["priority"], _wrap(t["title"], 29),
                        t["owner"] or "?"))
        L.append("")

    # --- complete -----------------------------------------------------------
    done = st.get("completed") or []
    L.append(" COMPLETE  (%d)" % len(done))
    L.append(_rule())
    if not done:
        L.append("   nothing finished yet")
    for t in done:
        sha = (t["commit_hash"] or "")[:7]
        state = commit_state.get(t["commit_hash"], "")
        if not sha:
            mark = "no commit recorded"
        elif state == "pushed":
            mark = "%s PUSHED" % sha
        elif state == "local":
            mark = "%s LOCAL ONLY" % sha
        elif state == "missing":
            mark = "%s NOT IN REPO" % sha
        else:
            mark = sha
        L.append("  %-10s %-35s %-8s %s"
                 % (t["task_id"], _wrap(t["title"], 35), t["completed_by"] or "?",
                    mark))
        if t["completion_summary"]:
            L.append("  %-10s %s" % ("", _wrap(t["completion_summary"], 59)))
    L.append("")

    # --- conflicts + activity ----------------------------------------------
    if st["conflicts"]:
        L.append(" CONFLICTS")
        L.append(_rule())
        for c in st["conflicts"]:
            L.append("  %-8s %-34s %s" % (c["level"], _wrap(c["path"], 34), c["why"]))
        L.append("")
    L.append(" RECENT")
    L.append(_rule())
    for e in st["recent_events"][:6]:
        L.append("  %s  %-9s %-20s %s"
                 % (_clock(e["ts"]), e["actor"] or "-", e["kind"],
                    _wrap(e["subject"] or "", 26)))
    L.append("=" * _W)
    return "\n".join(L)


def _enable_ansi():
    """Turn on VT processing so the cursor codes work in a Windows console.

    Without this the board prints raw escape sequences on stock cmd.exe. Windows
    Terminal and PowerShell 7 already have it on; enabling it twice is harmless.
    """
    if os.name != "nt":
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not k.GetConsoleMode(h, ctypes.byref(mode)):
            return False
        return bool(k.SetConsoleMode(h, mode.value | 0x0004))
    except Exception:
        return False


def _paint(lines, prev_len):
    """Redraw in place: home the cursor, overwrite, clear the tail.

    Deliberately NOT clear-screen-then-write. A full clear flashes on every
    frame, and this thing is meant to sit open on a second monitor for hours --
    a board that strobes once a second is one you stop looking at. Padding each
    line and clearing from the cursor down leaves the screen stable and only
    changes the characters that actually changed.
    """
    buf = ["\033[H"]
    for ln in lines:
        buf.append(ln + "\033[K\n")
    if prev_len > len(lines):
        buf.append("\033[J")
    sys.stdout.write("".join(buf))
    sys.stdout.flush()
    return len(lines)


def _board(a):
    st = core.project_status(project=a.project)
    st["completed"] = core.list_tasks(status="COMPLETE", project=a.project)
    states = core.commit_states([t["commit_hash"] for t in st["completed"]])
    return st, states


def cmd_monitor(a):
    if a.json:
        st, states = _board(a)
        st["commit_states"] = states
        out(a, st)
        return

    if not a.watch:
        st, states = _board(a)
        print(render_monitor(st, states))
        return

    interval = max(1, a.watch)
    ansi = _enable_ansi()
    if ansi:
        sys.stdout.write("\033[2J")            # one clear, at the start only
    painted, last_body, last_change = 0, None, time.time()
    try:
        while True:
            try:
                st, states = _board(a)
                body = render_monitor(st, states).splitlines()
                degraded = None
            except Exception as exc:
                # A locked or missing database must not kill a board that has
                # been open for hours. Say so on the board and keep polling.
                body = ["=" * _W,
                        " AGOPS MONITOR - COORDINATION UNREADABLE",
                        "=" * _W, "",
                        "  %s" % str(exc)[:70],
                        "",
                        "  Work is unaffected; this view is blind, not broken.",
                        "  Retrying every %ds. Ctrl-C to stop." % interval]
                degraded = True
            key = [l for l in body if not l.startswith(" AGOPS GCS")]
            if key != last_body:
                last_body, last_change = key, time.time()
            foot = ("  watching every %ds  |  last change %s ago  |  %s  |  Ctrl-C to stop"
                    % (interval, _hms(time.time() - last_change),
                       time.strftime("%H:%M:%S")))
            if degraded:
                foot = "  retrying  |  %s  |  Ctrl-C to stop" % time.strftime("%H:%M:%S")
            frame = body + [foot]
            if ansi:
                painted = _paint(frame, painted)
            else:
                # Dumb terminal or a pipe: append frames instead of repainting,
                # and FLUSH -- piped stdout is block-buffered, so without this
                # a monitor redirected to a file or a log shows nothing until
                # the process exits, which for this loop means never.
                print("\n".join(frame))
                sys.stdout.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        if ansi:
            sys.stdout.write("\033[J")
        print("\nmonitor stopped.")
        return


def out(args, obj, text=None):
    if getattr(args, "json", False):
        print(json.dumps(obj, indent=2, default=str))
    else:
        print(text if text is not None else json.dumps(obj, indent=2, default=str))


# --- commands ----------------------------------------------------------------

def cmd_register(a):
    r = core.register_agent(session_id=a.session or os.environ.get("CLAUDE_SESSION_ID"),
                            name=a.name, specialties=a.specialty or None,
                            role=a.role, cwd=os.getcwd(), pid_=os.getpid(),
                            project=a.project)
    ag = r["agent"]
    out(a, r, "%s as %s (%s)" % ("registered" if r["created"] else "re-attached",
                                 ag["name"], ag["agent_id"][:12]))


def cmd_whoami(a):
    who = current_agent(a.agent)
    if not who:
        out(a, {"agent": None}, "not registered in this session")
        return
    ags = [x for x in core.list_agents() if x["name"] == who]
    out(a, ags[0] if ags else {"agent": who},
        "%s  status=%s  task=%s" % (who, ags[0]["status"] if ags else "?",
                                    (ags[0]["current_task"] if ags else None) or "-"))


def cmd_status(a):
    st = core.project_status(project=a.project)
    out(a, st, render_status(st))


def cmd_agents(a):
    ags = core.list_agents(include_offline=a.all, project=a.project)
    out(a, ags, "\n".join("%-10s %-10s %-10s quiet %s%s"
                          % (x["name"], x["status"], x["current_task"] or "-",
                             _hms(x["quiet_s"]), "  STALE" if x["stale"] else "")
                          for x in ags) or "(none)")


def cmd_tasks(a):
    ts = core.list_tasks(status=a.status.upper() if a.status else None,
                         owner=a.owner, project=a.project)
    out(a, ts, "\n".join("%-10s %-12s %-8s %-10s %s"
                         % (t["task_id"], t["status"], t["priority"],
                            t["owner"] or "-", t["title"][:40]) for t in ts) or "(none)")


def cmd_task(a):
    t = core.get_task(a.task_id, project=a.project)
    txt = ["%s  %s  [%s/%s]" % (t["task_id"], t["title"], t["status"], t["priority"]),
           "owner:        %s" % (t["owner"] or "-"),
           "area:         %s" % (t["area"] or "-"),
           "depends on:   %s" % (", ".join(t["dependencies"]) or "-"),
           "unblocks:     %s" % (", ".join(t["dependents"]) or "-"),
           "files:        %s" % (", ".join(t["affected_files"]) or "-")]
    if t["blocked_reason"]:
        txt.append("blocked:      %s" % t["blocked_reason"])
    if t["description"]:
        txt.append("")
        txt.append(t["description"])
    if t["completion_summary"]:
        txt.append("")
        txt.append("DONE by %s: %s" % (t["completed_by"], t["completion_summary"]))
        if t["commit_hash"]:
            txt.append("commit %s" % t["commit_hash"])
    out(a, t, "\n".join(txt))


def cmd_create(a):
    t = core.create_task(a.title, description=a.description or "",
                         priority=a.priority.upper(), depends_on=a.depends_on or [],
                         files=a.file or [], area=a.area,
                         created_by=current_agent(a.agent) or "human",
                         estimate=a.estimate, task_id=a.id, project=a.project)
    out(a, t, "%s created (%s)" % (t["task_id"], t["status"]))


def cmd_next(a):
    who = current_agent(a.agent)
    ts = core.next_tasks(agent=who, limit=a.limit, project=a.project)
    if not ts:
        out(a, [], "no available work" +
            ("" if who else " (register to get specialty ranking)"))
        return
    out(a, ts, "\n".join("%-10s %-8s %-9s %s%s"
                         % (t["task_id"], t["priority"], t["_conflict"],
                            t["title"][:38],
                            "  spec+%d" % t["_specialty_match"]
                            if t["_specialty_match"] else "") for t in ts))


def cmd_claim(a):
    who = need_agent(a)
    r = core.claim_task(a.task_id, who, force=a.force, project=a.project)
    if r.get("ok"):
        msg = "CLAIMED %s as %s" % (a.task_id, r["owner"])
        for w in r.get("warnings") or []:
            msg += "\n  warning: %s" % w["why"]
        out(a, r, msg)
    else:
        out(a, r, r.get("message") or "claim failed: %s" % r.get("reason"))
        sys.exit(1)


def cmd_assign(a):
    r = core.assign_task(a.task_id, a.to, by=current_agent(a.agent) or "human",
                         note=a.note or "", project=a.project)
    msg = "ASSIGNED %s to %s" % (a.task_id, r["owner"])
    for c in r["conflicts"]:
        msg += "\n  %s: %s" % (c["level"], c["why"])
    out(a, r, msg)


def cmd_release(a):
    out(a, core.release_task(a.task_id, need_agent(a), a.reason or "",
                             project=a.project), "released %s" % a.task_id)


def cmd_complete(a):
    who = need_agent(a)
    tests = None
    if a.tests_passed:
        tests = True
    if a.tests_failed:
        tests = False
    r = core.complete_task(a.task_id, who, a.summary, verification=a.verification or "",
                           commit_hash=a.commit or "", tests_passed=tests,
                           project=a.project)
    msg = "COMPLETE %s" % a.task_id
    if r["unblocked"]:
        msg += "\n  now available: %s" % ", ".join(r["unblocked"])
    out(a, r, msg)


def cmd_block(a):
    core.block_task(a.task_id, need_agent(a), a.reason, project=a.project)
    out(a, {"ok": True}, "BLOCKED %s: %s" % (a.task_id, a.reason))


def cmd_unblock(a):
    t = core.unblock_task(a.task_id, current_agent(a.agent) or "human", project=a.project)
    out(a, t, "%s is now %s" % (a.task_id, t["status"]))


def cmd_message(a):
    who = current_agent(a.agent) or "human"
    r = core.send_message(who, a.to, a.content, msg_type=a.type.upper(),
                          related_task=a.task, related_files=a.file or [],
                          project=a.project)
    out(a, r, "sent %s to %s" % (r["type"], r["to"]))


def cmd_broadcast(a):
    who = current_agent(a.agent) or "human"
    r = core.send_message(who, "ALL", a.content, msg_type=a.type.upper(),
                          related_task=a.task, project=a.project)
    out(a, r, "broadcast sent")


def cmd_inbox(a):
    who = need_agent(a)
    msgs = core.inbox(who, unread_only=not a.all, mark_read=not a.peek,
                      project=a.project)
    if not msgs:
        out(a, [], "no messages")
        return
    txt = []
    for m in reversed(msgs):
        txt.append("[%s] %s -> %s%s\n%s" %
                   (m["msg_type"], m["sender"], m["recipient"],
                    ("  (%s)" % m["related_task"]) if m["related_task"] else "",
                    m["content"]))
    out(a, msgs, "\n\n".join(txt))


def cmd_handoff(a):
    who = need_agent(a)
    r = core.request_handoff(who, a.to, a.task_id, a.state, a.changed, a.remaining,
                             problems=a.problems or "", files=a.file or [],
                             tests=a.tests or "", next_action=a.next or "",
                             project=a.project)
    out(a, r, "handed %s to %s" % (a.task_id, a.to))


def cmd_conflicts(a):
    r = core.check_conflicts(a.file, agent=current_agent(a.agent), project=a.project)
    out(a, r, "%s\n%s" % (r["level"],
                          "\n".join("  %-8s %s  %s" % (c["level"], c["path"], c["why"])
                                    for c in r["conflicts"]) or "  (no conflicts)"))


def cmd_owners(a):
    r = core.get_file_owners(a.path, project=a.project)
    out(a, r, "%s  area=%s  owners=%s  level=%s"
        % (r["path"], r["area"] or "-", ", ".join(r["owners"]) or "nobody", r["level"]))


def cmd_heartbeat(a):
    r = core.heartbeat(need_agent(a), status=a.status.upper() if a.status else None,
                       note=a.note, project=a.project)
    out(a, r, "%s %s" % (r["name"], r["status"]))


def cmd_offline(a):
    out(a, core.unregister_agent(a.who or need_agent(a), project=a.project), "ok")


def cmd_recover(a):
    r = core.recover(agent=a.who, dry_run=not a.apply, project=a.project)
    if not r["recoverable"]:
        out(a, r, "nothing to recover")
        return
    lines = []
    for e in r["recoverable"]:
        lines.append("%s quiet %s holds %s" % (e["agent"], _hms(e["quiet_s"]),
                                               ", ".join(e["tasks"])))
        lines.append("  git: %d dirty file(s) at %s -- PRESERVED, nothing discarded"
                     % (e["git"]["dirty_count"], e["git"]["head"]))
    if not a.apply:
        lines.append("\n(dry run -- re-run with --apply to flag these for review)")
    out(a, r, "\n".join(lines))


def cmd_reclaim(a):
    t = core.reclaim(a.task_id, need_agent(a), verified=a.verified, project=a.project)
    out(a, t, "reclaimed %s" % a.task_id)


def cmd_take(a):
    r = core.take_resource(a.resource, need_agent(a), a.reason or "", project=a.project)
    out(a, r, r.get("message") or "took %s" % a.resource)
    if not r.get("ok"):
        sys.exit(1)


def cmd_drop(a):
    out(a, core.drop_resource(a.resource, need_agent(a), project=a.project),
        "dropped %s" % a.resource)


def cmd_admin(a):
    cfg = core.load_config()
    if a.action == "pause":
        cfg["coordination_enabled"] = False
    elif a.action == "resume":
        cfg["coordination_enabled"] = True
    elif a.action == "clear-agents":
        # Wipes the roster so the NATO names start at alpha again. Refuses while
        # anyone still holds work: that is either live work or a crash worth
        # recovering, and silently forgetting who had it is how you lose both.
        conn = core.connect()
        try:
            held = [dict(r) for r in conn.execute(
                "SELECT name, current_task FROM agents WHERE current_task IS NOT NULL")]
            if held and not a.force:
                raise AgopsError(
                    "these agents still hold work: %s. Finish, release "
                    "(admin release --task-id X) or recover it first, or pass "
                    "--force to clear the roster anyway (tasks keep their owner)."
                    % ", ".join("%s/%s" % (h["name"], h["current_task"]) for h in held))
            n = conn.execute("SELECT COUNT(*) c FROM agents").fetchone()["c"]
            conn.execute("DELETE FROM agents")
            core._event(conn, "human", "team.clear_agents", "",
                        "cleared %d agent(s)" % n)
        finally:
            conn.close()
        out(a, {"ok": True, "cleared": n}, "cleared %d agent(s); names restart at alpha" % n)
        return
    elif a.action == "policy":
        if a.value not in ("assigned", "on_request", "self_serve"):
            raise AgopsError("policy takes assigned | on_request | self_serve")
        cfg["claim_policy"] = a.value
    elif a.action == "auto-claim":
        if a.value not in ("on", "off"):
            raise AgopsError("auto-claim takes on | off")
        cfg["auto_claim"] = (a.value == "on")
    elif a.action == "enforcement":
        if a.value not in ("advisory", "blocking", "off"):
            raise AgopsError("enforcement must be advisory | blocking | off")
        cfg["enforcement"] = a.value
    elif a.action == "assign":
        # Both arguments are load-bearing and both were previously unchecked:
        # `assign ""` cheerfully set an IN_PROGRESS task to owner-nobody, which
        # is a state no other code path can produce and none of them expect.
        if not a.task_id:
            raise AgopsError("assign needs --task-id")
        if not (a.value or "").strip():
            raise AgopsError(
                "assign needs an agent name. To take a task away from its owner "
                "without giving it to anybody, use: admin release --task-id %s"
                % a.task_id)
        conn = core.connect()
        try:
            if core._resolve_agent(conn, a.value) is None:
                raise AgopsError("no agent named %r on this project" % a.value)
            core.get_task(a.task_id)          # raises if the task does not exist
            conn.execute("UPDATE tasks SET owner=?, status='IN_PROGRESS', updated_at=? "
                         "WHERE task_id=?", (a.value, core._now(), a.task_id))
            core._event(conn, "human", "task.reassign", a.task_id, "to " + a.value)
        finally:
            conn.close()
        out(a, {"ok": True}, "%s reassigned to %s" % (a.task_id, a.value))
        return
    elif a.action == "release":
        # The override that was missing: hand a task back to the pool regardless
        # of who holds it. Needed whenever an agent is simply gone and there is
        # nothing to recover -- a throwaway session, a cancelled experiment.
        if not a.task_id:
            raise AgopsError("release needs --task-id")
        core.get_task(a.task_id)
        conn = core.connect()
        try:
            conn.execute("UPDATE tasks SET owner=NULL, status='AVAILABLE', "
                         "claimed_at=NULL, needs_recovery=0, updated_at=? "
                         "WHERE task_id=?", (core._now(), a.task_id))
            # Clear the agent-side pointer too. Leaving it set showed an OFFLINE
            # agent still holding a task the board had already handed back --
            # two answers to "who has this", which is how trust in a board dies.
            conn.execute("UPDATE agents SET current_task=NULL WHERE current_task=?",
                         (a.task_id,))
            core._event(conn, "human", "task.force_release", a.task_id,
                        a.value or "human override")
        finally:
            conn.close()
        out(a, {"ok": True}, "%s released to the pool" % a.task_id)
        return
    elif a.action == "cancel":
        conn = core.connect()
        try:
            conn.execute("UPDATE tasks SET status='CANCELLED', updated_at=? "
                         "WHERE task_id=?", (core._now(), a.task_id))
            core._event(conn, "human", "task.cancel", a.task_id, a.value or "")
        finally:
            conn.close()
        out(a, {"ok": True}, "%s cancelled" % a.task_id)
        return
    elif a.action == "clear-locks":
        conn = core.connect()
        try:
            conn.execute("UPDATE resources SET holder=NULL")
            core._event(conn, "human", "resource.clear_all", "")
        finally:
            conn.close()
        out(a, {"ok": True}, "all resource locks cleared")
        return
    else:
        raise AgopsError("unknown admin action %r" % a.action)
    core.save_config(cfg)
    out(a, cfg, "coordination_enabled=%s enforcement=%s claim_policy=%s auto_claim=%s"
        % (cfg["coordination_enabled"], cfg["enforcement"],
           cfg.get("claim_policy", "assigned"), cfg.get("auto_claim", False)))


def cmd_events(a):
    conn = core.connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (a.limit,))]
    finally:
        conn.close()
    out(a, rows, "\n".join("%-10s %-22s %-12s %s"
                           % (r["actor"] or "-", r["kind"], r["subject"] or "",
                              (r["detail"] or "")[:40]) for r in rows) or "(none)")


def cmd_doctor(a):
    """Is coordination actually working? Never lie about this."""
    report = {"db": False, "config": False, "writable": False, "project": None,
              "agents": 0, "warnings": []}
    try:
        cfg = core.load_config()
        report["config"] = os.path.exists(core.CONFIG_PATH)
        report["project"] = cfg.get("project_id")
        conn = core.connect()
        conn.execute("SELECT 1 FROM meta LIMIT 1")
        report["db"] = True
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('doctor',?)",
                     (str(core._now()),))
        report["writable"] = True
        report["agents"] = len(core.list_agents())
        conn.close()
    except Exception as exc:
        report["warnings"].append(str(exc))
    if not report["config"]:
        report["warnings"].append(
            ".agops/project.json missing -- defaults in use")
    if not core.load_config().get("coordination_enabled", True):
        report["warnings"].append("coordination is PAUSED by human override")
    ok = report["db"] and report["writable"]
    out(a, report,
        ("COORDINATION OK  project=%s agents=%d" % (report["project"], report["agents"]))
        if ok else "COORDINATION DEGRADED -- work normally, claims are not enforced"
        + "".join("\n  ! " + w for w in report["warnings"]))
    if not ok:
        sys.exit(1)


# --- parser ------------------------------------------------------------------

def build_parser():
    # The common flags are attached to the top level AND to every subcommand, so
    # both `agops --json status` and `agops status --json` work. Agents type the
    # second form by reflex, and an argparse usage error there reads like the
    # coordinator is broken rather than like a typo.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true",
                        help="machine-readable output")
    common.add_argument("--project", help="project id (isolation check)")
    common.add_argument("--agent", help="act as this agent")

    p = argparse.ArgumentParser(prog="agops", parents=[common],
                                description="AgOps team coordination")
    _sub = p.add_subparsers(dest="cmd", required=True)

    class sub:                      # every subparser inherits the common flags
        @staticmethod
        def add_parser(name, **kw):
            kw.setdefault("parents", [common])
            return _sub.add_parser(name, **kw)

    s = sub.add_parser("register", help="register/re-attach this session")
    s.add_argument("--name"); s.add_argument("--session")
    s.add_argument("--specialty", action="append")
    s.add_argument("--role", choices=["lead", "worker"])
    s.set_defaults(fn=cmd_register)

    sub.add_parser("whoami").set_defaults(fn=cmd_whoami)
    sub.add_parser("status", help="team dashboard").set_defaults(fn=cmd_status)

    s = sub.add_parser("monitor", help="full board: sessions, tasks, commits")
    s.add_argument("--watch", type=int, nargs="?", const=2, metavar="SECONDS",
                   help="live board: repaint every N seconds (default 2) until "
                        "Ctrl-C. Run this in its own terminal.")
    s.set_defaults(fn=cmd_monitor)
    sub.add_parser("doctor", help="is coordination working").set_defaults(fn=cmd_doctor)

    s = sub.add_parser("agents"); s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_agents)

    s = sub.add_parser("tasks"); s.add_argument("--status"); s.add_argument("--owner")
    s.set_defaults(fn=cmd_tasks)

    s = sub.add_parser("task"); s.add_argument("task_id"); s.set_defaults(fn=cmd_task)

    s = sub.add_parser("create", help="create a task")
    s.add_argument("title"); s.add_argument("--description")
    s.add_argument("--priority", default="MEDIUM")
    s.add_argument("--depends-on", action="append")
    s.add_argument("--file", action="append", help="affected file or glob")
    s.add_argument("--area"); s.add_argument("--estimate"); s.add_argument("--id")
    s.set_defaults(fn=cmd_create)

    s = sub.add_parser("next", help="ranked available work")
    s.add_argument("--limit", type=int, default=5); s.set_defaults(fn=cmd_next)

    s = sub.add_parser("claim"); s.add_argument("task_id")
    s.add_argument("--force", action="store_true",
                   help="claim despite a BLOCKING conflict (only after agreeing)")
    s.set_defaults(fn=cmd_claim)

    s = sub.add_parser("assign", help="dispatch a task to an agent (human control)")
    s.add_argument("task_id"); s.add_argument("to", help="agent name")
    s.add_argument("--note", help="anything the agent should know first")
    s.set_defaults(fn=cmd_assign)

    s = sub.add_parser("release"); s.add_argument("task_id"); s.add_argument("--reason")
    s.set_defaults(fn=cmd_release)

    s = sub.add_parser("complete"); s.add_argument("task_id")
    s.add_argument("summary"); s.add_argument("--verification")
    s.add_argument("--commit"); s.add_argument("--tests-passed", action="store_true")
    s.add_argument("--tests-failed", action="store_true")
    s.set_defaults(fn=cmd_complete)

    s = sub.add_parser("block"); s.add_argument("task_id"); s.add_argument("reason")
    s.set_defaults(fn=cmd_block)
    s = sub.add_parser("unblock"); s.add_argument("task_id"); s.set_defaults(fn=cmd_unblock)

    s = sub.add_parser("message"); s.add_argument("to"); s.add_argument("content")
    s.add_argument("--type", default="INFO"); s.add_argument("--task")
    s.add_argument("--file", action="append"); s.set_defaults(fn=cmd_message)

    s = sub.add_parser("broadcast"); s.add_argument("content")
    s.add_argument("--type", default="INFO"); s.add_argument("--task")
    s.set_defaults(fn=cmd_broadcast)

    s = sub.add_parser("inbox"); s.add_argument("--all", action="store_true")
    s.add_argument("--peek", action="store_true", help="do not mark read")
    s.set_defaults(fn=cmd_inbox)

    s = sub.add_parser("handoff"); s.add_argument("task_id"); s.add_argument("to")
    s.add_argument("--state", required=True); s.add_argument("--changed", required=True)
    s.add_argument("--remaining", required=True); s.add_argument("--problems")
    s.add_argument("--file", action="append"); s.add_argument("--tests")
    s.add_argument("--next"); s.set_defaults(fn=cmd_handoff)

    s = sub.add_parser("conflicts"); s.add_argument("file", nargs="+")
    s.set_defaults(fn=cmd_conflicts)
    s = sub.add_parser("owners"); s.add_argument("path"); s.set_defaults(fn=cmd_owners)

    s = sub.add_parser("heartbeat"); s.add_argument("--status"); s.add_argument("--note")
    s.set_defaults(fn=cmd_heartbeat)
    s = sub.add_parser("offline"); s.add_argument("who", nargs="?")
    s.set_defaults(fn=cmd_offline)

    s = sub.add_parser("recover"); s.add_argument("who", nargs="?")
    s.add_argument("--apply", action="store_true"); s.set_defaults(fn=cmd_recover)
    s = sub.add_parser("reclaim"); s.add_argument("task_id")
    s.add_argument("--verified", action="store_true"); s.set_defaults(fn=cmd_reclaim)

    s = sub.add_parser("take"); s.add_argument("resource"); s.add_argument("--reason")
    s.set_defaults(fn=cmd_take)
    s = sub.add_parser("drop"); s.add_argument("resource"); s.set_defaults(fn=cmd_drop)

    s = sub.add_parser("admin", help="human override")
    s.add_argument("action", choices=["pause", "resume", "enforcement", "policy",
                                      "auto-claim", "assign", "release",
                                      "cancel", "clear-locks", "clear-agents"])
    s.add_argument("value", nargs="?"); s.add_argument("--task-id")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_admin)

    s = sub.add_parser("events"); s.add_argument("--limit", type=int, default=25)
    s.set_defaults(fn=cmd_events)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
        return 0
    except AgopsError as exc:
        print("agops: %s" % exc, file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())

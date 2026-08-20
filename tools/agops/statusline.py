#!/usr/bin/env python
"""Status line: which agent is this window, and what is it holding.

Four terminals that look identical is a real operational hazard -- the name is
printed once at session start and then scrolls away, so telling alpha from
charlie means scrolling back or running whoami. This puts it on every prompt.

Shows, in order of how much it matters when it is wrong:

  * unread messages   -- bravo once sat idle for six minutes holding three
                         unread, one of which was the instruction unblocking it.
                         Nothing can wake a stopped session, so the only fix is
                         making the count impossible to miss.
  * resource locks    -- sitl-5760 is single-occupancy and the usual cause of a
                         red scenario is contention, not a regression.
  * the current task  -- and OFF-BOARD when the session is editing with no task
                         held, which is the state rule 1 exists to prevent.

Fails silent and prints nothing rather than an error: a status line that breaks
the prompt is worse than no status line. It never writes to the database -- a
prompt render is not evidence of liveness, and a heartbeat from here would make
an abandoned terminal look busy forever.
"""
import json
import os
import sqlite3
import sys

C = {"dim": "\033[2m", "red": "\033[31m", "grn": "\033[32m", "yel": "\033[33m",
     "cyn": "\033[36m", "mag": "\033[35m", "off": "\033[0m", "bold": "\033[1m"}


def paint(s, *names):
    if os.environ.get("NO_COLOR"):
        return s
    return "".join(C[n] for n in names) + s + C["off"]


def build(payload):
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    db = os.path.join(here, ".agops", "agops.db")
    if not os.path.exists(db):
        return ""

    sid = payload.get("session_id") or ""
    if not sid:
        return ""
    conn = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True, timeout=0.4)
    conn.row_factory = sqlite3.Row
    try:
        me = conn.execute(
            "SELECT name, status, current_task FROM agents "
            "WHERE session_id=? OR agent_id=?", (sid, sid)).fetchone()
        if me is None:
            return paint("not on the board", "dim")

        name, status, task = me["name"], me["status"], me["current_task"]
        bits = [paint(name, "bold", "cyn")]

        unread = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE read_at IS NULL "
            "AND (recipient=? OR recipient='ALL') AND sender<>?",
            (name, name)).fetchone()[0]
        if unread:
            bits.append(paint("%d unread" % unread, "bold", "red"))

        if task:
            row = conn.execute("SELECT title, priority FROM tasks WHERE task_id=?",
                               (task,)).fetchone()
            title = (row["title"] if row else "")[:34]
            colour = "red" if (row and row["priority"] == "CRITICAL") else "grn"
            bits.append(paint(task, colour) + paint(" " + title, "dim"))
        elif status not in ("OFFLINE", "STARTING"):
            bits.append(paint("OFF-BOARD", "yel"))

        held = [r["name"] for r in conn.execute(
            "SELECT name FROM resources WHERE holder=?", (name,))]
        if held:
            bits.append(paint("holds " + ",".join(held), "mag"))

        waiting = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status='AVAILABLE'").fetchone()[0]
        if waiting:
            bits.append(paint("%d open" % waiting, "dim"))

        return paint(" | ", "dim").join(bits)
    finally:
        conn.close()


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        payload = {}
    try:
        line = build(payload)
    except Exception:
        line = ""
    if line:
        sys.stdout.write(line)


if __name__ == "__main__":
    main()

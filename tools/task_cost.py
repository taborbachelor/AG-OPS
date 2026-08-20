#!/usr/bin/env python3
"""Per-task token cost, joined from the AgOps board and the local transcripts.

session_cost.py answers "what did this SESSION cost"; this answers the finer
question VALUATION.md actually wants feeding: what did each TASK cost. It
joins each task's ownership window (claimed_at .. completed_at) against the
owning session's transcript and sums the usage priced at Anthropic list
price -- the same billing proxy, same rates, as session_cost.py.

    py tools\\task_cost.py                    # every task with a window
    py tools\\task_cost.py --task TASK-020    # one task
    py tools\\task_cost.py --json             # machine-readable

HONEST LIMITS, printed rather than hidden:
  * Attribution is by TIME WINDOW: everything the owning session spent while
    it held the task counts, including unrelated chatter in that window.
  * A transcript lives on the machine that ran the session. Tasks worked on
    another machine read "no transcript here" -- absence of a number, never a
    zero.
  * Workflow subagent spend is not attributed (their transcripts carry no
    task identity); totals are therefore a floor.
  * The join is by agent NAME via the roster's CURRENT session_id. After a
    roster recycle the name points at a NEW session, so tasks completed
    before the recycle price against the wrong (empty) window and read ~0.
    Trust rows only for work done by the roster generation that did it.
"""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)

from agops import core  # noqa: E402
from session_cost import RATES, FALLBACK, price  # noqa: E402  (one price table)


def _transcript_root():
    # Overridable so the test suite can point this at a fixture instead of
    # the real profile.
    override = os.environ.get("AGOPS_TRANSCRIPTS")
    return Path(override) if override else Path.home() / ".claude" / "projects"


def _find_transcript(session_id):
    root = _transcript_root()
    if not session_id or not root.exists():
        return None
    hits = list(root.glob("*/%s.jsonl" % session_id)) + \
        list(root.glob("%s.jsonl" % session_id))
    return hits[0] if hits else None


def _iso_epoch(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")) \
            .astimezone(timezone.utc).timestamp()
    except (ValueError, AttributeError):
        return None


def window_cost(path, start, end):
    """Priced usage of one transcript inside [start, end]."""
    tokens = defaultdict(Counter)
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            ts = _iso_epoch(rec.get("timestamp") or "")
            if ts is None or not (start <= ts <= end):
                continue
            usage = (rec.get("message") or {}).get("usage")
            if not usage:
                continue
            t = tokens[(rec.get("message") or {}).get("model", "unknown")]
            t["in"] += usage.get("input_tokens", 0)
            t["out"] += usage.get("output_tokens", 0)
            t["cache_read"] += usage.get("cache_read_input_tokens", 0)
            created = usage.get("cache_creation") or {}
            if created:
                t["cache_write_5m"] += created.get("ephemeral_5m_input_tokens", 0) or 0
                t["cache_write_1h"] += created.get("ephemeral_1h_input_tokens", 0) or 0
            else:
                t["cache_write_5m"] += usage.get("cache_creation_input_tokens", 0)
    cost = sum(price(m, t) for m, t in tokens.items())
    toks = sum(t["in"] + t["out"] for t in tokens.values())
    return cost, toks


def task_rows(only=None):
    conn = core.connect()
    try:
        q = ("SELECT * FROM tasks WHERE claimed_at IS NOT NULL"
             + (" AND task_id=?" if only else "") + " ORDER BY task_id")
        tasks = [dict(r) for r in conn.execute(q, (only,) if only else ())]
        sessions = {r["name"]: r["session_id"]
                    for r in conn.execute("SELECT name, session_id FROM agents")}
    finally:
        conn.close()
    rows = []
    for t in tasks:
        owner = t["completed_by"] or t["owner"]
        start = t["claimed_at"]
        end = t["completed_at"] or datetime.now(timezone.utc).timestamp()
        row = {"task_id": t["task_id"], "owner": owner or "?",
               "status": t["status"], "hours": (end - start) / 3600.0,
               "cost": None, "tokens": None, "note": ""}
        path = _find_transcript(sessions.get(owner or ""))
        if path is None:
            row["note"] = "no transcript here"
        else:
            row["cost"], row["tokens"] = window_cost(path, start, end)
        rows.append(row)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--task", help="one task id")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rows = task_rows(only=a.task)
    if a.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("no tasks with an ownership window")
        return 0
    print("%-10s %-9s %-12s %7s %10s %10s  %s"
          % ("task", "owner", "status", "hours", "tokens", "cost", ""))
    total = 0.0
    for r in rows:
        cost = "$%.2f" % r["cost"] if r["cost"] is not None else "--"
        toks = ("%dk" % (r["tokens"] // 1000)) if r["tokens"] else "--"
        print("%-10s %-9s %-12s %7.1f %10s %10s  %s"
              % (r["task_id"], r["owner"], r["status"], r["hours"],
                 toks, cost, r["note"]))
        total += r["cost"] or 0.0
    print("%-51s %10s %10s" % ("", "total:", "$%.2f" % total))
    print("\nwindow attribution: whole-session spend inside each task's "
          "ownership window;\nsubagent spend excluded; other machines' "
          "sessions invisible; tasks from BEFORE\na roster recycle price "
          "~0 (the name now maps to a new session). A floor, not a bill.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
"""SessionStart hook: tell a new session its own id and the current claim state.

Without this a session cannot know its `session_id`, and claiming under the
wrong one would get it blocked from its own files by tools/guard.py. Emitting it
here removes the guesswork for every session started after this hook exists;
sessions already running when it was installed use the CLAIM_WHOAMI handshake in
guard.py instead.

Fails open and silent -- a broken session-start hook should never stop a session.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    session = payload.get("session_id") or "unknown"

    try:
        res = subprocess.run(
            [sys.executable, os.path.join(HERE, "claim.py"), "status", "-v"],
            capture_output=True, text=True, timeout=15)
        status = (res.stdout or "").strip()
    except Exception:
        status = "(claim registry unavailable)"

    context = (
        "AgOps GCS repo. Concurrent Claude sessions are kept off each other's files by a "
        "claim registry plus a PreToolUse hook that BLOCKS edits into another session's area.\n\n"
        "YOUR session_id is: %s\n"
        "Use exactly that string as --session in every claim.py command.\n\n"
        "Before editing anything:\n"
        "  1. Read LANES.md (areas, seam register, decisions log).\n"
        "  2. py tools\\claim.py status -v          # what's free\n"
        "  3. py tools\\claim.py claim --session %s --area <AIR|PLANNER|UI|OPS|DOCS> "
        "--label \"<what you're building>\"\n"
        "  4. Fill in your block in LANES.md, then work only inside your area.\n\n"
        "Taking the SITL port: py tools\\claim.py take --session %s --resource sitl-5760 "
        "(drop it when done).\n"
        "Releasing at session end: py tools\\claim.py release --session %s\n\n"
        "Current registry state:\n%s"
    ) % (session, session, session, session, status)

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

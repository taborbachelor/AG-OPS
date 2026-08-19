#!/usr/bin/env python
"""PreToolUse hook: refuse an edit that lands in another session's claimed area.

Wired from .claude/settings.json (project-level, so it is git-tracked and every
session and worktree inherits it). Reads the hook payload on stdin, works out
which file the tool is about to modify, and asks tools/claim.py whether this
session owns it.

Two deliberate asymmetries:

* **Fail OPEN on internal error.** A bug in this file must never brick every
  session's ability to edit. If anything here throws, the edit proceeds.
* **Fail CLOSED on a real conflict.** If the registry positively says another
  live session owns that path, the edit is blocked (exit 2, stderr goes back to
  Claude as the reason).

Solo sessions are unaffected: claim.py returns "allow" whenever fewer than two
sessions are registered.
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CLAIM = os.path.join(HERE, "claim.py")

# Bash/PowerShell is only inspected when the command actually mutates something.
# Read-only greps and cats over another lane's files are fine and common.
#
# The redirect clause deliberately excludes two things that look like writes and
# are not: fd duplication (`2>&1`, `>&2`) and `/dev/null`. Without those
# exclusions a plain `pytest ... 2>&1 | tail` reads as a mutation, and every
# path mentioned anywhere on the line gets checked -- which blocked a read-only
# test run on this very repo.
MUTATORS = re.compile(
    r"(^|[\s;|&])(sed\s+-i|rm\b|mv\b|cp\b|tee\b|truncate\b|dd\b|"
    r"npm\s+(run\s+)?build|pyinstaller|git\s+(checkout|restore|reset|clean|apply|revert))"
    r"|>>?\s*(?!&)(?!/dev/null)(?!NUL)\S", re.I)

PATHISH = re.compile(r"[\w./\\-]+\.(py|jsx|js|ts|tsx|css|html|ps1|sh|json|md|spec)\b")


def _strip_heredocs(cmd: str) -> str:
    """Drop heredoc BODIES before scanning.

    A heredoc body is data, not command: `git commit -F - <<'EOF' ... EOF`
    carries a commit message that may name any number of files it is merely
    describing. Scanning it produced confident, wrong blocks -- a commit
    message mentioning config.py read as an attempt to write config.py.
    Only the command line itself decides what is being touched.
    """
    out, lines = [], cmd.splitlines()
    i, n = 0, len(lines)
    while i < n:
        out.append(lines[i])
        m = re.search(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1", lines[i])
        if m:
            tag = m.group(2)
            i += 1
            while i < n and lines[i].strip() != tag:
                i += 1          # body: skipped entirely
            if i < n:
                i += 1          # and the terminator
            continue
        i += 1
    return "\n".join(out)


def _targets(tool, ti):
    """Files this tool call is about to write. Empty = nothing to guard."""
    if tool in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
        p = ti.get("file_path") or ti.get("notebook_path")
        return [p] if p else []
    if tool in ("Bash", "PowerShell"):
        cmd = _strip_heredocs(ti.get("command") or "")
        if not MUTATORS.search(cmd):
            return []
        return list(dict.fromkeys(m.group(0) for m in PATHISH.finditer(cmd)))
    return []


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0                                   # unparseable -> not our business

    try:
        session = payload.get("session_id") or "unknown"
        tool = payload.get("tool_name") or ""
        ti = payload.get("tool_input") or {}

        # Identity handshake. A session does not otherwise know its own
        # session_id, and claiming under the wrong one would get it blocked from
        # its own files. It runs `echo CLAIM_WHOAMI:<tag>`; we catch that here --
        # where the real id is -- and drop it in a file named by the tag, so
        # concurrent handshakes can't collide.
        if tool in ("Bash", "PowerShell"):
            m = re.search(r"CLAIM_WHOAMI:([A-Za-z0-9_-]{1,32})", ti.get("command") or "")
            if m:
                d = os.path.join(REPO, ".claim")
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, "whoami-%s.txt" % m.group(1)),
                          "w", encoding="utf-8") as fh:
                    fh.write(session)
                return 0

        for target in _targets(tool, ti):
            if not os.path.isabs(target):
                target = os.path.join(payload.get("cwd") or REPO, target)
            # Only guard files inside THIS repo.
            if os.path.commonpath([os.path.abspath(target), REPO]) != REPO:
                continue
            res = subprocess.run(
                [sys.executable, CLAIM, "check", "--session", session,
                 "--path", os.path.abspath(target)],
                capture_output=True, text=True, timeout=15)
            if res.returncode == 1:
                sys.stderr.write(res.stderr or "BLOCKED by claim registry.\n")
                return 2                           # 2 = block, stderr is the reason
    except Exception:
        return 0                                   # fail open, always

    return 0


if __name__ == "__main__":
    sys.exit(main())

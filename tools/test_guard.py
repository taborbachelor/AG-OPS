"""Regression tests for the parallel-session guard.

Run: py tools\\test_guard.py    (stdlib only, no pytest, no repo imports)

Every case here is a bug that actually fired during the 2026-08-19 three-session
run and blocked real work. The guard is only useful if it is boring: a false
block trains sessions to route around it, which is worse than not having it.

Deliberately NOT under backend/tests/: this tests session tooling, not the
aircraft. `pytest` in backend/ should stay the flight-software suite.
"""
import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
GUARD = os.path.join(HERE, "guard.py")

spec = importlib.util.spec_from_file_location("guard_under_test", GUARD)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

FAILS = []


def check(label, cond):
    print("%-4s %s" % ("OK" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label)


P = "backend/app/coverage.py"

# --- mutating-command detection -----------------------------------------
# Read-only work across areas must stay allowed; reading another area's code is
# normal and useful.
for cmd, want, label in [
    ("grep -n foo " + P, False, "read-only grep"),
    ("cat " + P, False, "read-only cat"),
    ("python -m pytest tests/", False, "plain test run"),
    ("pytest -q 2" + ">&1 | tail", False, "fd duplication 2>&1"),
    (">" + "&2 echo oops", False, "fd duplication >&2"),
    ("cat x 2" + ">" + "/dev/null", False, "redirect to /dev/null"),
    ("print('bank -" + "> load factor')", False, "ASCII arrow, not a redirect"),
    ("a =" + "> b", False, "fat arrow, not a redirect"),
    ("sed -i s/a/b/ " + P, True, "sed -i"),
    ("echo hi " + ">" + " " + P, True, "real redirect"),
    ("echo hi " + ">>" + " " + P, True, "real append"),
    ("rm " + P, True, "rm"),
    ("mv " + P + " /tmp/x", True, "mv"),
    ("npm run build", True, "npm run build"),
    ("git checkout -- " + P, True, "git checkout --"),
]:
    check("mutator: " + label, bool(g.MUTATORS.search(cmd)) == want)

# --- heredoc bodies are data, not command -------------------------------
# A commit message that merely NAMES a file used to read as writing it.
HEREDOC = ("git commit -F - <<'EOF'\nSubject\n\n"
           "config.py and tools/claim.py are only mentioned here.\nEOF")
check("heredoc body is not scanned for targets",
      g._targets("Bash", {"command": HEREDOC}) == [])
check("real mutation alongside a heredoc is still caught",
      "backend/app/x.py" in g._targets(
          "Bash", {"command": "rm backend/app/x.py && cat <<'EOF'\nnoise.py\nEOF"}))

# --- cd-relative path resolution ----------------------------------------
# `cd backend && sed -i ... app/guardian.py` is backend/app/guardian.py, not
# <repo>/app/guardian.py -- which matched nobody and blocked its real owner.
payload = {
    "session_id": "guard-selftest-session-not-a-real-claim",
    "tool_name": "Bash",
    "cwd": REPO,
    "tool_input": {"command": "cd %s/backend && sed -i s/a/b/ app/guardian.py"
                              % REPO.replace("\\", "/")},
}
res = subprocess.run([sys.executable, GUARD], input=json.dumps(payload),
                     capture_output=True, text=True)
check("cd-relative path resolves under backend/",
      "app/guardian.py" not in res.stderr or "backend/app/guardian.py" in res.stderr)

# --- fail-open guarantees -----------------------------------------------
# A bug in the guard must never brick every session's ability to edit.
for label, stdin in [("malformed stdin", "not json"),
                     ("empty stdin", ""),
                     ("no tool_input", json.dumps({"session_id": "x",
                                                   "tool_name": "Edit"}))]:
    r = subprocess.run([sys.executable, GUARD], input=stdin,
                       capture_output=True, text=True)
    check("fails open: " + label, r.returncode == 0)

# --- paths outside the repo are none of our business --------------------
r = subprocess.run(
    [sys.executable, GUARD],
    input=json.dumps({"session_id": "x", "tool_name": "Edit", "cwd": REPO,
                      "tool_input": {"file_path": r"C:\Users\jacks\CLAUDE.md"}}),
    capture_output=True, text=True)
check("file outside the repo is allowed", r.returncode == 0)

print()
if FAILS:
    print("%d FAILURES:" % len(FAILS))
    for f in FAILS:
        print("  - " + f)
    raise SystemExit(1)
print("ALL PASS")

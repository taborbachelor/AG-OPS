#!/usr/bin/env python
r"""Is the packaged exe current, and what went into it?

TASK-005 closed as COMPLETE with no commit recorded. Nobody could say whether
the binary existed or what was in it, and that stayed true for a day. TASK-014
answered it by hand and found the binary was DANGEROUS: built before 6c7a692, it
shipped powerline exclusion fences that were stored and never enforced, so a
bench day on it would have flown with the operator's keepouts inert.

Then it happened again within hours -- bca1bc6 landed and the freshly rebuilt
binary was stale before anyone noticed.

The cause is structural, not a lapse. `dist/` is gitignored, so the artifact has
no commit, no provenance, and no way to be known stale; every check is prose in a
doc that ages silently. This makes the question mechanical.

    py tools\exe_status.py            report
    py tools\exe_status.py --strict   exit 1 if stale or unknown (for CI)
    py tools\exe_status.py --stamp    record the current HEAD as the build point

`--stamp` writes backend/BUILD-PROVENANCE.json, which IS tracked -- that is the
whole point. Run it immediately after pyinstaller, in the same breath.

What counts as shipped code is deliberately narrow: `backend/app` and
`frontend/src`. A commit touching only tests, tools or docs does not stale a
binary, and a checker that cries stale on a docs commit is one people stop
running.
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "backend", "dist", "AgOpsGCS.exe")
STAMP = os.path.join(ROOT, "backend", "BUILD-PROVENANCE.json")
SHIPPED = ["backend/app", "frontend/src"]


def git(*args):
    out = subprocess.run(["git", "-C", ROOT] + list(args),
                         capture_output=True, text=True, timeout=30)
    return out.stdout.strip() if out.returncode == 0 else ""


def head():
    return git("rev-parse", "HEAD")


def stamp():
    sha = head()
    if not sha:
        print("not a git checkout -- nothing to stamp")
        return 1
    if not os.path.exists(EXE):
        print("no binary at %s -- build it before stamping" % EXE)
        return 1
    data = {
        "built_from": sha,
        "subject": git("log", "-1", "--format=%s", sha),
        "built_at": git("log", "-1", "--format=%cI", sha),
        "size_bytes": os.path.getsize(EXE),
        "note": ("Written by tools/exe_status.py --stamp. dist/ is gitignored, "
                 "so this file is the binary's only provenance. Re-stamp on "
                 "every rebuild, immediately after pyinstaller."),
    }
    with open(STAMP, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    print("stamped %s at %s" % (os.path.relpath(STAMP, ROOT), sha[:7]))
    return 0


def main():
    if "--stamp" in sys.argv:
        return stamp()
    strict = "--strict" in sys.argv

    print("=" * 70)
    print(" PACKAGED EXE")
    print("=" * 70)

    if not os.path.exists(EXE):
        print("\n  NO BINARY. A Cube bench day has nothing to run.")
        print("  Build:  cd frontend && npm run build")
        print("          cd backend  && .\\venv\\Scripts\\pyinstaller.exe "
              "AgOpsGCS.spec --noconfirm")
        print("  Then:   py tools\\exe_status.py --stamp")
        return 1 if strict else 0

    size = os.path.getsize(EXE) / 1e6
    if not os.path.exists(STAMP):
        print("\n  UNKNOWN PROVENANCE. The binary exists (%.1f MB) and nothing"
              % size)
        print("  records what went into it -- exactly the TASK-005 state.")
        print("  If you built it from the current tree, run --stamp now.")
        print("  If you did not, rebuild rather than guess: a binary from before")
        print("  6c7a692 carries exclusion fences that are stored, not enforced.")
        return 1 if strict else 0

    with open(STAMP, encoding="utf-8") as fh:
        rec = json.load(fh)
    built = rec.get("built_from", "")
    now = head()

    print("\n  binary      %.1f MB" % size)
    print("  built from  %s  %s" % (built[:7], rec.get("subject", "")))
    print("  HEAD        %s" % now[:7])

    if built == now:
        print("\n  CURRENT -- built from HEAD.")
        return 0

    missing = git("log", "--oneline", "%s..HEAD" % built, "--", *SHIPPED)
    if not missing:
        behind = git("log", "--oneline", "%s..HEAD" % built)
        n = len(behind.splitlines()) if behind else 0
        print("\n  CURRENT for shipping purposes -- %d commit(s) since the build,"
              % n)
        print("  none touching %s." % " or ".join(SHIPPED))
        return 0

    lines = missing.splitlines()
    print("\n  STALE. %d commit(s) to shipped code are NOT in this binary:"
          % len(lines))
    for ln in lines:
        print("      %s" % ln)
    print("\n  Rebuild, then re-stamp:")
    print("      cd frontend && npm run build")
    print("      cd backend  && .\\venv\\Scripts\\pyinstaller.exe "
          "AgOpsGCS.spec --noconfirm")
    print("      py tools\\exe_status.py --stamp")
    print("\n  Kill-by-name gotcha: the onefile bootloader spawns a child, so")
    print("  killing the launched pid leaves the server on :8000.")
    return 1 if strict else 0


if __name__ == "__main__":
    sys.exit(main())

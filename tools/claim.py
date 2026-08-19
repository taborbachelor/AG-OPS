#!/usr/bin/env python
"""Work-claim registry: keeps N concurrent Claude sessions off each other's files.

The registry is the machine-readable truth; LANES.md is the human view of it.
Claims live in <repo>/.claim/ (gitignored), one JSON file per session, guarded by
an O_EXCL mutex so two sessions cannot claim the same area in the same instant.

Design notes that matter:

* **Solo is free.** With fewer than two live sessions registered, nothing is
  enforced -- there is no one to collide with. Friction only appears when a
  second session shows up.
* **Claims expire.** Every claim carries a heartbeat; `check` (which the
  PreToolUse hook calls on every edit) renews the caller's own claim. A session
  that dies goes stale on its own and its area frees up. No manual cleanup.
* **Overlap is computed, not eyeballed.** `claim` expands globs against the real
  tracked file list and intersects the sets, so two areas that share a single
  file are caught before either session starts typing.
* **Overrides are audited, not prevented.** Any agent with write access can edit
  any file on disk; this is accident-prevention between cooperating sessions, not
  a security boundary. What it guarantees is that a deliberate override is
  recorded in .claim/audit.log, which is git-tracked and shows up in a diff.

Usage:
  py tools\\claim.py status
  py tools\\claim.py areas
  py tools\\claim.py claim --session <id> --area AIR [--label "onboard fences"]
  py tools\\claim.py claim --session <id> --glob "backend/app/coverage*.py"
  py tools\\claim.py take --session <id> --resource sitl-5760
  py tools\\claim.py check --session <id> --path backend/app/guardian.py
  py tools\\claim.py release --session <id>
  py tools\\claim.py grant --session <id> --area UI --token <token> --reason "..."
"""
import argparse
import fnmatch
import json
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLAIM_DIR = os.path.join(REPO, ".claim")
MUTEX = os.path.join(CLAIM_DIR, ".registry.lock")
AUDIT = os.path.join(CLAIM_DIR, "audit.log")
TOKEN_FILE = os.path.join(CLAIM_DIR, "override.token")

TTL_S = 90 * 60          # a claim goes stale this long after its last heartbeat
MUTEX_STALE_S = 30       # a held mutex older than this is assumed abandoned

# Work areas. Deliberately file-disjoint: see LANES.md. Add one here rather than
# inventing ad-hoc globs, so overlap detection stays meaningful across sessions.
AREAS = {
    "AIR": {
        "desc": "Onboard enforcement: link, guardian, mission/fence upload, SITL scenarios",
        "globs": [
            "backend/app/vehicle_manager.py",
            "backend/app/guardian.py",
            "backend/app/keepout_watch.py",
            "backend/app/preflight.py",
            "backend/app/param_meta.py",
            "backend/app/config.py",
            "backend/app/eventlog.py",
            "backend/app/onboard_fence.py",
            "backend/app/routers/safety.py",
            "backend/app/routers/mission.py",
            "backend/app/routers/vehicle.py",
            "backend/app/routers/logs.py",
            "backend/app/routers/bench.py",
            "backend/app/routers/sim.py",
            "backend/tests/sitl/*",
            "backend/tests/test_guardian.py",
            "backend/tests/test_keepout_watch.py",
            "backend/tests/test_preflight.py",
            "backend/tests/test_bench.py",
            "backend/tests/test_scorecard.py",
            "backend/tests/test_m1*.py",
            "backend/tests/test_m2_link.py",
            "backend/tests/test_m3_params.py",
            "backend/tests/test_m4_sim.py",
            "backend/tests/test_flight_*.py",
            "backend/scenarios.ps1",
        ],
    },
    "PLANNER": {
        "desc": "Coverage planning, GIS, hazards, field detection",
        "globs": [
            "backend/app/coverage.py",
            "backend/app/coverage_multi.py",
            "backend/app/reroute.py",
            "backend/app/gis_zones.py",
            "backend/app/cdl.py",
            "backend/app/albers.py",
            "backend/app/field_boundaries.py",
            "backend/app/routers/coverage.py",
            "backend/app/routers/coverage_multi.py",
            "backend/app/routers/fields.py",
            "backend/tests/test_coverage*.py",
            "backend/tests/test_reroute.py",
            "backend/tests/test_hazard_reroute_planning.py",
            "backend/tests/test_zones*.py",
            "backend/tests/test_cdl.py",
            "backend/tests/test_multi.py",
        ],
    },
    "UI": {
        "desc": "GCS operator frontend (all of it)",
        "globs": ["frontend/*", "frontend/**"],
    },
    "OPS": {
        "desc": "Customer site, orders, tooling, launcher, packaging",
        "globs": [
            "web/*", "web/**",
            "backend/app/routers/orders.py",
            "backend/app/routers/connection.py",
            "backend/app/main.py",
            "backend/tests/test_orders.py",
            "backend/AgOpsGCS.spec",
            "backend/run_gcs.py",
            "tools/*",
            "start-all.ps1",
        ],
    },
    "DOCS": {
        "desc": "Root design + reference docs (NOT LANES.md, which is always shared)",
        "globs": [
            "README.md", "ARCHITECTURE.md", "GAP-ANALYSIS.md",
            "SPRAY-FLIGHT-SAFETY.md", "POWERLINE-KEEPOUTS.md",
            "VALUATION.md", "CLAUDE-CALEB.md",
        ],
    },
}

# Exclusive non-file resources. One holder at a time, same registry.
RESOURCES = {
    "sitl-5760": "The SITL TCP port. Single-occupancy: scenarios.ps1 / pytest -m sitl",
    "serial-fc": "The real Cube over USB/COM",
    "exe-build": "PyInstaller output at backend/dist/AgOpsGCS.exe",
    "git-push":  "Pushing to origin/main (take briefly if you hit races)",
}

# Never guarded: the coordination surfaces themselves, plus anything untracked
# and disposable. LANES.md is shared on purpose -- every session writes its own
# block there, and the block-ownership rule (not the guard) keeps that safe.
ALWAYS_OPEN = ["LANES.md", ".claim/*", ".claim/**", "*.log"]


# ---------------------------------------------------------------- primitives

def _now():
    return int(time.time())


def _ensure_dir():
    if not os.path.isdir(CLAIM_DIR):
        os.makedirs(CLAIM_DIR, exist_ok=True)


class Mutex:
    """Cross-process mutex via O_EXCL. Windows-safe (no fcntl)."""

    def __enter__(self):
        _ensure_dir()
        deadline = time.time() + 10
        while True:
            try:
                fd = os.open(MUTEX, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(MUTEX) > MUTEX_STALE_S:
                        os.unlink(MUTEX)      # holder died mid-write
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    raise SystemExit("claim: registry busy (mutex held >10s)")
                time.sleep(0.05)

    def __exit__(self, *exc):
        try:
            os.unlink(MUTEX)
        except OSError:
            pass


def _path(session):
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in session)[:64]
    return os.path.join(CLAIM_DIR, safe + ".json")


def _load_all(reap=True):
    """Every live claim. Stale ones are removed as a side effect."""
    _ensure_dir()
    out, now = [], _now()
    for name in sorted(os.listdir(CLAIM_DIR)):
        if not name.endswith(".json"):
            continue
        full = os.path.join(CLAIM_DIR, name)
        try:
            with open(full, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        if now - rec.get("heartbeat", 0) > rec.get("ttl_s", TTL_S):
            if reap:
                try:
                    os.unlink(full)
                    _audit("expire", rec.get("session", "?"),
                           "stale for %ds" % (now - rec.get("heartbeat", 0)))
                except OSError:
                    pass
            continue
        out.append(rec)
    return out


def _save(rec):
    tmp = _path(rec["session"]) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=2)
    os.replace(tmp, _path(rec["session"]))       # atomic


def _audit(action, session, detail):
    _ensure_dir()
    line = "%s\t%s\t%s\t%s\n" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), action, session, detail)
    with open(AUDIT, "a", encoding="utf-8") as fh:
        fh.write(line)


# ---------------------------------------------------------------- matching

def _globs_for(areas, globs):
    out = []
    for a in areas:
        if a not in AREAS:
            raise SystemExit("claim: unknown area %r (try: %s)"
                             % (a, ", ".join(sorted(AREAS))))
        out.extend(AREAS[a]["globs"])
    out.extend(globs)
    return out


def _matches(rel, globs):
    rel = rel.replace("\\", "/").lstrip("./")
    for g in globs:
        if fnmatch.fnmatch(rel, g):
            return True
        # "frontend/**" should also cover "frontend/src/App.jsx"
        if g.endswith("/**") and rel.startswith(g[:-3] + "/"):
            return True
        if g.endswith("/*") and rel.startswith(g[:-2] + "/"):
            return True
    return False


def _tracked():
    try:
        out = subprocess.run(["git", "-C", REPO, "ls-files"],
                             capture_output=True, text=True, timeout=20)
        return [l.strip() for l in out.stdout.splitlines() if l.strip()]
    except Exception:
        return []


def _expand(globs, files):
    return {f for f in files if _matches(f, globs)}


def _overlap(globs_a, globs_b):
    """Concrete overlap between two glob sets: real files first, then glob shape.

    The file-set intersection is exact for everything that exists today; the
    string comparison additionally catches two claims aimed at the same
    not-yet-created file (e.g. both planning to add onboard_fence.py)."""
    files = _tracked()
    shared = sorted(_expand(globs_a, files) & _expand(globs_b, files))
    if shared:
        return shared
    for ga in globs_a:
        for gb in globs_b:
            if ga == gb:
                return [ga]
            for x, y in ((ga, gb), (gb, ga)):
                base = x[:-3] if x.endswith("/**") else x[:-2] if x.endswith("/*") else None
                if base and (y == base or y.startswith(base + "/")):
                    return [y]
    return []


# ---------------------------------------------------------------- commands

def cmd_areas(_args):
    print("Areas:")
    for name, spec in sorted(AREAS.items()):
        print("  %-8s %s" % (name, spec["desc"]))
        print("           %d globs" % len(spec["globs"]))
    print("\nResources (exclusive, one holder):")
    for name, desc in sorted(RESOURCES.items()):
        print("  %-12s %s" % (name, desc))


def cmd_status(args):
    claims = _load_all()
    if not claims:
        print("No active claims. Guard is INERT (solo mode: <2 sessions).")
        return
    print("%d active session(s)%s\n" % (
        len(claims),
        "" if len(claims) > 1 else "  -- guard INERT until a 2nd session registers"))
    now = _now()
    for c in claims:
        age = (now - c["created"]) // 60
        beat = (now - c["heartbeat"]) // 60
        print("  [%s] %s" % (c["session"][:12], c.get("label") or "(no label)"))
        print("      areas:     %s" % (", ".join(c["areas"]) or "-"))
        if c["globs"]:
            print("      globs:     %s" % ", ".join(c["globs"]))
        if c.get("resources"):
            print("      resources: %s" % ", ".join(c["resources"]))
        if c.get("grants"):
            print("      GRANTED:   %s  (override)" % ", ".join(c["grants"]))
        print("      age %dm, last beat %dm ago" % (age, beat))
    if args.verbose:
        print("\nFree areas: %s" % ", ".join(
            sorted(set(AREAS) - {a for c in claims for a in c["areas"]})) or "none")


def cmd_claim(args):
    want = _globs_for(args.area, args.glob)
    if not want:
        raise SystemExit("claim: nothing to claim (pass --area and/or --glob)")
    with Mutex():
        claims = _load_all()
        mine = next((c for c in claims if c["session"] == args.session), None)
        for other in claims:
            if other["session"] == args.session:
                continue
            allowed = set(other.get("grants", []))
            clash = [p for p in _overlap(want, other["globs"])
                     if p not in allowed]
            if clash:
                print("DENIED: overlaps session %s (%s)"
                      % (other["session"][:12], other.get("label") or "no label"))
                print("  contested: %s" % ", ".join(clash[:8]))
                if len(clash) > 8:
                    print("  ...and %d more" % (len(clash) - 8))
                print("\n  Pick a free area (py tools\\claim.py status -v), or ask")
                print("  Tabor to authorise an overlap:")
                print("    py tools\\claim.py grant --session %s --area <A> "
                      "--token <token> --reason \"...\"" % args.session)
                _audit("denied", args.session, "clash with %s on %s"
                       % (other["session"][:12], ",".join(clash[:5])))
                return 1
        rec = mine or {
            "session": args.session, "created": _now(),
            "areas": [], "globs": [], "resources": [], "grants": [],
        }
        rec["areas"] = sorted(set(rec.get("areas", [])) | set(args.area))
        rec["globs"] = sorted(set(rec.get("globs", [])) | set(want))
        rec["label"] = args.label or rec.get("label", "")
        rec["heartbeat"] = _now()
        rec["ttl_s"] = TTL_S
        rec["pid"] = os.getpid()
        _save(rec)
        _audit("claim", args.session, "%s %s" % (",".join(args.area), args.label or ""))
    n = len(_load_all())
    print("CLAIMED %s%s" % (", ".join(args.area) or "globs",
                            "" if not args.glob else " + %d glob(s)" % len(args.glob)))
    print("Guard is %s (%d session%s registered)."
          % ("ACTIVE" if n > 1 else "INERT", n, "" if n == 1 else "s"))
    return 0


def cmd_take(args):
    with Mutex():
        claims = _load_all()
        for other in claims:
            if other["session"] != args.session and args.resource in other.get("resources", []):
                print("DENIED: %s is held by %s (%s), %dm ago"
                      % (args.resource, other["session"][:12],
                         other.get("label") or "no label",
                         (_now() - other["heartbeat"]) // 60))
                return 1
        rec = next((c for c in claims if c["session"] == args.session), None)
        if rec is None:
            raise SystemExit("claim: claim an area first, then take a resource")
        rec["resources"] = sorted(set(rec.get("resources", [])) | {args.resource})
        rec["heartbeat"] = _now()
        _save(rec)
        _audit("take", args.session, args.resource)
    print("TOOK %s -- release it as soon as you're done." % args.resource)
    return 0


def cmd_drop(args):
    with Mutex():
        claims = _load_all()
        rec = next((c for c in claims if c["session"] == args.session), None)
        if rec:
            rec["resources"] = [r for r in rec.get("resources", []) if r != args.resource]
            rec["heartbeat"] = _now()
            _save(rec)
            _audit("drop", args.session, args.resource)
    print("RELEASED %s" % args.resource)
    return 0


def cmd_check(args):
    """Hot path: the PreToolUse hook calls this on every edit. Keep it cheap."""
    rel = os.path.relpath(os.path.abspath(args.path), REPO).replace("\\", "/")
    if rel.startswith(".."):
        return 0                                   # outside the repo, not ours
    if _matches(rel, ALWAYS_OPEN):
        return 0
    claims = _load_all(reap=False)
    if len(claims) < 2:
        return 0                                   # solo: nobody to collide with
    mine = next((c for c in claims if c["session"] == args.session), None)
    for other in claims:
        if other["session"] == args.session:
            continue
        if _matches(rel, other["globs"]):
            if mine and rel in [g for g in mine.get("grants", [])]:
                return 0
            if mine and _matches(rel, mine.get("grants", [])):
                return 0
            sys.stderr.write(
                "BLOCKED: %s belongs to session %s (%s).\n"
                "Another Claude session is working that area right now.\n"
                "  See who has what:  py tools\\claim.py status\n"
                "  Claim a free area: py tools\\claim.py claim --session %s --area <AREA>\n"
                "  If Tabor explicitly authorised an overlap, he grants it:\n"
                "    py tools\\claim.py grant --session %s --glob %s --token <token> "
                "--reason \"...\"\n"
                % (rel, other["session"][:12], other.get("label") or "no label",
                   args.session, args.session, rel))
            return 1
    if mine and _matches(rel, mine["globs"]):
        if _now() - mine["heartbeat"] > 60:        # cheap heartbeat, once a minute
            mine["heartbeat"] = _now()
            try:
                _save(mine)
            except OSError:
                pass
        return 0
    if not mine:
        sys.stderr.write(
            "BLOCKED: this session holds no claim, and %d other session(s) are active.\n"
            "Claim an area before editing: py tools\\claim.py claim --session %s --area <AREA>\n"
            "(py tools\\claim.py areas  lists them; status -v shows what's free)\n"
            % (len(claims), args.session))
        return 1
    sys.stderr.write(
        "BLOCKED: %s is outside your claim (%s), and other sessions are active.\n"
        "Widen it deliberately: py tools\\claim.py claim --session %s --glob \"%s\"\n"
        % (rel, ", ".join(mine["areas"]) or "globs only", args.session, rel))
    return 1


def cmd_grant(args):
    """Tabor-authorised overlap. Recorded in the git-tracked audit log."""
    expected = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, encoding="utf-8") as fh:
            expected = fh.read().strip()
    if not expected:
        raise SystemExit(
            "claim: no override token set. Tabor creates one:\n"
            "  py tools\\claim.py set-token --token <something-only-you-know>")
    if args.token != expected:
        _audit("grant-refused", args.session, "bad token")
        raise SystemExit("claim: bad override token -- refusing.")
    if not args.reason:
        raise SystemExit("claim: --reason is required for an override")
    want = _globs_for(args.area, args.glob)
    with Mutex():
        claims = _load_all()
        rec = next((c for c in claims if c["session"] == args.session), None)
        if rec is None:
            raise SystemExit("claim: session has no claim to extend")
        rec["grants"] = sorted(set(rec.get("grants", [])) | set(want))
        rec["heartbeat"] = _now()
        _save(rec)
        _audit("GRANT", args.session, "%s :: %s" % (",".join(want), args.reason))
    print("GRANTED (override) %s" % ", ".join(want))
    print("Logged to .claim/audit.log (local). Also add a row to LANES.md's decisions")
    print("log -- that is the git-tracked record, and it survives this machine.")
    return 0


def cmd_set_token(args):
    _ensure_dir()
    with open(TOKEN_FILE, "w", encoding="utf-8") as fh:
        fh.write(args.token.strip())
    print("Override token set (%s). It is gitignored." % TOKEN_FILE)
    return 0


def cmd_release(args):
    with Mutex():
        try:
            os.unlink(_path(args.session))
            _audit("release", args.session, "")
            print("RELEASED all claims for %s" % args.session[:12])
        except OSError:
            print("No claim held by %s" % args.session[:12])
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("areas").set_defaults(fn=cmd_areas)

    p = sub.add_parser("status"); p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("claim")
    p.add_argument("--session", required=True)
    p.add_argument("--area", action="append", default=[])
    p.add_argument("--glob", action="append", default=[])
    p.add_argument("--label", default="")
    p.set_defaults(fn=cmd_claim)

    p = sub.add_parser("take"); p.add_argument("--session", required=True)
    p.add_argument("--resource", required=True, choices=sorted(RESOURCES))
    p.set_defaults(fn=cmd_take)

    p = sub.add_parser("drop"); p.add_argument("--session", required=True)
    p.add_argument("--resource", required=True, choices=sorted(RESOURCES))
    p.set_defaults(fn=cmd_drop)

    p = sub.add_parser("check"); p.add_argument("--session", required=True)
    p.add_argument("--path", required=True)
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("grant"); p.add_argument("--session", required=True)
    p.add_argument("--area", action="append", default=[])
    p.add_argument("--glob", action="append", default=[])
    p.add_argument("--token", required=True)
    p.add_argument("--reason", default="")
    p.set_defaults(fn=cmd_grant)

    p = sub.add_parser("set-token"); p.add_argument("--token", required=True)
    p.set_defaults(fn=cmd_set_token)

    p = sub.add_parser("release"); p.add_argument("--session", required=True)
    p.set_defaults(fn=cmd_release)

    args = ap.parse_args()
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()

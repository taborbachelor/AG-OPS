#!/usr/bin/env python
r"""Find backend surfaces nothing calls.

This repo's signature failure has a shape. Four times now a route has been built
end to end -- handler, tests, wiring, all green -- and nothing ever called it:

  * POST /api/safety/keepouts   the live keepout-proximity monitor ran with zero
                                rings and could never warn. Both halves were
                                individually "done"; the seam was owned by
                                nobody. Fixed in 86c6a6e.
  * the post-flight scorecard   written on every disarm, served on GET
                                /api/logs/{name}, invisible to the operator.
  * the turn-geometry stats     every plan reported turn_bank_ok and nothing
                                rendered it -- including the false case, where
                                the operator most needs telling.

The standing mitigation is a doc habit: "do a deliberate seam pass at the end of
every parallel session". A failure that has already happened four times deserves
a check, not a reminder.

METHOD MATTERS, and it is the whole reason this is not a grep. When the keepout
monitor was broken, GET /api/safety/keepouts *was* called from the frontend --
only POST was orphaned. A path-only search calls that route reachable and misses
the exact bug this exists to catch.

BASES MATTER for the same reason. Not every caller builds on api.js's API: the
customer site declares its own (const API_BASE = '/api/orders'), and assuming
otherwise reports four live order endpoints as orphaned. A checker with false
positives is one everybody learns to scroll past.

Honest about its blind spots rather than quiet about them: a call site that
builds its path at runtime cannot be resolved statically, so those are listed
separately instead of being silently counted as coverage.

    py tools\seam_check.py              every orphan
    py tools\seam_check.py --ui         only routes no OPERATOR can reach
    py tools\seam_check.py --strict     exit 1 if any orphan is found (for CI)
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CALLER_DIRS = [("frontend/src", "ui"), ("web", "ui"), ("backend/tests", "test")]
METHODS = ("get", "post", "put", "patch", "delete")
SKIP_DIRS = {"node_modules", "__pycache__", "build", "dist", "venv", ".git"}
WILDCARD = "\x00*"


def segments(path):
    """Path -> comparable segments. A dynamic segment matches anything."""
    path = path.split("?")[0].split("#")[0].strip("/")
    if not path:
        return []
    out = []
    for seg in path.split("/"):
        dynamic = ("{" in seg) or ("$" in seg) or ("+" in seg) or seg.startswith(":")
        out.append(WILDCARD if dynamic else seg)
    return out


def same(a, b):
    return len(a) == len(b) and all(
        x == y or WILDCARD in (x, y) for x, y in zip(a, b))


def read(p):
    with open(p, encoding="utf-8", errors="replace") as f:
        return f.read()


def walk(rel, exts):
    for dirpath, dirnames, files in os.walk(os.path.join(ROOT, rel)):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in files:
            if fn.endswith(exts):
                yield os.path.join(dirpath, fn)


def find_routes():
    """Every mounted route, as method + segments + where it is declared."""
    main = read(os.path.join(ROOT, "backend/app/main.py"))
    prefixes = {}
    pat = r"include_router\(\s*(\w+)\.router\s*,\s*prefix=[\"']([^\"']+)[\"']"
    for mod, pref in re.findall(pat, main):
        prefixes.setdefault(mod, []).append(pref)

    routes = []
    for mod, prefs in prefixes.items():
        path = os.path.join(ROOT, "backend/app/routers", mod + ".py")
        if not os.path.exists(path):
            continue
        src = read(path)
        rpat = r"@router\.(%s)\(\s*[\"']([^\"']*)[\"']" % "|".join(METHODS)
        for m in re.finditer(rpat, src):
            method, route = m.group(1).upper(), m.group(2)
            line = src[:m.start()].count("\n") + 1
            for pref in prefs:
                full = (pref + route).replace("//", "/") or pref
                routes.append({"method": method, "segs": segments(full),
                               "display": full,
                               "where": "backend/app/routers/%s.py:%d" % (mod, line)})
    return routes


def js_bases(src):
    """Map this file's base constants to the paths they stand for."""
    bases = {"API": "/api", "WS_BASE": "/api"}
    pat = r"""(?:const|let|var)\s+(\w+)\s*=\s*['"]([^'"]*/api[^'"]*)['"]"""
    for name, val in re.findall(pat, src):
        bases[name] = val.rstrip("/")
    return bases


def method_near(src, at):
    """fetch's method rides in an options object after the url, or it is GET."""
    m = re.search(r"""method\s*:\s*['"](\w+)['"]""", src[at:at + 400])
    return (m.group(1) if m else "GET").upper()


def find_calls():
    """Every resolvable call site, plus the ones that defeat static reading."""
    calls, murky = [], []

    for rel, kind in CALLER_DIRS:
        if not os.path.isdir(os.path.join(ROOT, rel)):
            continue
        exts = (".py",) if kind == "test" else (".js", ".jsx", ".ts", ".tsx", ".html")
        for path in walk(rel, exts):
            src = read(path)
            show = os.path.relpath(path, ROOT).replace("\\", "/")

            def add(method, url, at, _src=src, _show=show, _kind=kind):
                calls.append({"method": method, "segs": segments(url), "kind": _kind,
                              "where": "%s:%d" % (_show, _src[:at].count("\n") + 1)})

            if kind == "test":
                tpat = r"""\.(%s)\(\s*['"]([^'"]*/api/[^'"]*)['"]""" % "|".join(METHODS)
                for m in re.finditer(tpat, src):
                    add(m.group(1).upper(), m.group(2), m.start())
                continue

            bases = js_bases(src)

            for m in re.finditer(r"""\$\{(\w+)\}([^`'"]*)""", src):
                name, tail = m.group(1), m.group(2)
                if name not in bases:
                    continue
                if tail.startswith("$"):
                    murky.append("%s:%d  path built at runtime from ${%s}"
                                 % (show, src[:m.start()].count("\n") + 1, name))
                    continue
                add(method_near(src, m.end()), bases[name] + tail, m.start())

            for m in re.finditer(r"""['"`](/api/[^'"`]*)['"`]""", src):
                add(method_near(src, m.end()), m.group(1), m.start())

    return calls, murky


def find_orphans(routes, calls, ui_only=False):
    """Routes with no caller, as (route, kinds-that-do-call-it).

    Exported rather than inlined into main() so the tests drive this code and
    not a copy of it. A test that restates the implementation passes against
    the bug it was written to catch -- that is how the 3D orientation bugs
    survived until someone asked the real library where the nose ended up.
    """
    orphans = []
    for r in routes:
        hits = [c for c in calls
                if c["method"] == r["method"] and same(c["segs"], r["segs"])]
        shown = [c for c in hits if c["kind"] == "ui"] if ui_only else hits
        if not shown:
            orphans.append((r, {c["kind"] for c in hits}))
    return orphans


def main():
    ui_only = "--ui" in sys.argv
    strict = "--strict" in sys.argv
    routes = find_routes()
    calls, murky = find_calls()
    orphans = find_orphans(routes, calls, ui_only)

    print("=" * 74)
    print(" SEAM CHECK   %d routes   %d call sites   %d unresolvable"
          % (len(routes), len(calls), len(murky)))
    print("=" * 74)

    if orphans:
        head = ("NO OPERATOR CAN REACH THESE (no UI caller)" if ui_only
                else "NOTHING CALLS THESE")
        print("\n %s\n %s" % (head, "-" * 72))
        for r, kinds in sorted(orphans, key=lambda x: x[0]["display"]):
            note = "   tests call it, no UI does" if kinds else ""
            print("  %-6s %-38s %s%s" % (r["method"], r["display"], r["where"], note))
    else:
        print("\n  every route has a caller")

    if murky:
        print("\n BLIND SPOTS -- these build their path at runtime, so nothing here")
        print(" was checked. A route reached only this way looks orphaned above.")
        print(" " + "-" * 72)
        for m in sorted(set(murky)):
            print("  %s" % m)

    print("\n Method is matched, not just path: when the keepout monitor was broken,")
    print(" GET was called and only POST was orphaned.")
    print("=" * 74)
    return 1 if (strict and orphans) else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Compute Claude Code token cost + attended time for this project's sessions.

The point of this script is that VALUATION.md never has to be a rough stab
again: it scans the local Claude Code transcripts, keeps only the sessions
whose working directory was this project, and prints a row per session ready
to paste into the ledger.

    py tools\\session_cost.py                     # every session found
    py tools\\session_cost.py --since 2026-08-15  # only sessions starting on/after
    py tools\\session_cost.py --new               # only sessions NOT already in VALUATION.md
    py tools\\session_cost.py --session 69aaf91a  # one session, to correct a logged row
    py tools\\session_cost.py --json              # machine-readable

A session cannot fully count itself: the row you write during a session is
priced at that moment and the session keeps spending after you write it. So
the last row in VALUATION.md is always a little low. Correct it next session
with --session <its id>; --new will not surface it, because its id is already
in the file.

Costs are Anthropic API LIST PRICE, which is a billing proxy, not spend --
Claude Code actually runs on a Max plan. See VALUATION.md for why we track it
that way and why it is the floor of the project's value, not the price.

CAVEAT: transcripts live under the Windows profile of whatever machine ran the
session (~/.claude/projects/C--Users-<profile>-.../). Sessions run on a machine
you are not currently on are INVISIBLE to this script. Rows for those stay in
VALUATION.md by hand -- never let a re-run silently drop them.
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from glob import glob
from pathlib import Path

# --- Anthropic list price, USD per million tokens -------------------------
# Cache multipliers on the input rate: 5m write 1.25x, 1h write 2x, read 0.1x.
RATES = {
    "claude-fable-5":   (10.0, 50.0),
    "claude-mythos-5":  (10.0, 50.0),
    "claude-opus-5":    (5.0,  25.0),
    "claude-opus-4-8":  (5.0,  25.0),
    "claude-opus-4-7":  (5.0,  25.0),
    "claude-opus-4-6":  (5.0,  25.0),
    "claude-sonnet-5":  (3.0,  15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0,   5.0),
}
FALLBACK = (5.0, 25.0)  # unknown model -> price it as Opus tier

PROJECT_MARKER = "rc-plane"  # matches rc-plane-app and the Mission Planner fork
REPO_ROOT = Path(__file__).resolve().parent.parent
LEDGER = REPO_ROOT / "VALUATION.md"


def rate_for(model):
    for name, r in RATES.items():
        if model.startswith(name):
            return r
    return FALLBACK


def price(model, tok):
    tin, tout = rate_for(model)
    return (
        tok["in"] * tin
        + tok["out"] * tout
        + tok["cache_read"] * tin * 0.1
        + tok["cache_write_5m"] * tin * 1.25
        + tok["cache_write_1h"] * tin * 2.0
    ) / 1e6


def transcript_dirs():
    root = Path.home() / ".claude" / "projects"
    return sorted(p for p in root.glob("*") if p.is_dir()) if root.exists() else []


def scan(path, tokens, stamps, cwds):
    """Fold one .jsonl transcript into the accumulators."""
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("timestamp"):
                stamps.append(rec["timestamp"])
            if rec.get("cwd"):
                cwds[rec["cwd"]] += 1
            usage = (rec.get("message") or {}).get("usage")
            if not usage:
                continue
            model = (rec.get("message") or {}).get("model", "unknown")
            t = tokens[model]  # defaultdict(Counter): missing keys start at zero
            t["in"] += usage.get("input_tokens", 0)
            t["out"] += usage.get("output_tokens", 0)
            t["cache_read"] += usage.get("cache_read_input_tokens", 0)
            created = usage.get("cache_creation") or {}
            if created:
                t["cache_write_5m"] += created.get("ephemeral_5m_input_tokens", 0) or 0
                t["cache_write_1h"] += created.get("ephemeral_1h_input_tokens", 0) or 0
            else:
                t["cache_write_5m"] += usage.get("cache_creation_input_tokens", 0)


def collect():
    sessions = []
    for proj in transcript_dirs():
        for main in sorted(proj.glob("*.jsonl")):
            sid = main.stem
            tokens = defaultdict(Counter)
            stamps, cwds = [], Counter()

            scan(main, tokens, stamps, cwds)
            for extra in glob(str(proj / sid / "**" / "*.jsonl"), recursive=True):
                scan(extra, tokens, stamps, cwds)

            if not stamps or not cwds:
                continue

            # Share of the session actually spent in this project. A bare
            # "did any cwd mention rc-plane" test over-counts badly: one `cd`
            # into the repo from an unrelated session sticks for the rest of
            # it, which would bill a Relevyn evening to Caleb.
            on_project = sum(n for c, n in cwds.items() if PROJECT_MARKER in c.lower())
            share = on_project / sum(cwds.values())
            if share == 0:
                continue

            start, end = min(stamps), max(stamps)
            cost = sum(price(m, t) for m, t in tokens.items())
            sessions.append({
                "id": sid,
                "short": sid[:8],
                "start": start,
                "end": end,
                "hours": round(hours_between(start, end), 2),
                "share": round(share, 3),
                "models": sorted(tokens),
                "output_tokens": sum(t["out"] for t in tokens.values()),
                "cost": round(cost, 2),
                "profile": proj.name,
            })
    sessions.sort(key=lambda s: s["start"])
    return sessions


def hours_between(a, b):
    fmt = "%Y-%m-%dT%H:%M:%S"
    try:
        t0 = datetime.strptime(a[:19], fmt)
        t1 = datetime.strptime(b[:19], fmt)
    except ValueError:
        return 0.0
    return max((t1 - t0).total_seconds() / 3600.0, 0.0)


def already_logged():
    """Session id prefixes already present in VALUATION.md."""
    if not LEDGER.exists():
        return set()
    text = LEDGER.read_text(encoding="utf-8", errors="ignore")
    return set(re.findall(r"\b[0-9a-f]{8}\b", text))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", metavar="YYYY-MM-DD", help="only sessions starting on/after this date")
    ap.add_argument("--new", action="store_true", help="only sessions not already in VALUATION.md")
    ap.add_argument("--session", metavar="PREFIX",
                    help="just this session id (prefix match). Use it to CORRECT a row already "
                         "in the ledger -- notably the row for the session that wrote the ledger, "
                         "which always undercounts itself (see --new note below)")
    ap.add_argument("--min-share", type=float, default=0.5, metavar="F",
                    help="fraction of a session that must be in this project to count it "
                         "in the total (default 0.5); anything below is listed as mixed")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    rows = collect()
    if args.session:
        rows = [r for r in rows if r["id"].startswith(args.session)]
    if args.since:
        rows = [r for r in rows if r["start"][:10] >= args.since]
    if args.new:
        seen = already_logged()
        rows = [r for r in rows if r["short"] not in seen]

    if args.as_json:
        json.dump(rows, sys.stdout, indent=2)
        print()
        return

    if not rows:
        print("No matching sessions found on this machine.")
        print("Transcripts only exist under the profile of the machine that ran them.")
        return

    billable = [r for r in rows if r["share"] >= args.min_share]
    mixed = [r for r in rows if r["share"] < args.min_share]

    def table(items):
        for r in items:
            models = ", ".join(m.replace("claude-", "") for m in r["models"])
            print(f"{r['start'][:16]:<17}{r['hours']:>6.1f}{r['share']:>8.0%}  {r['short']:<10}"
                  f"{'$' + format(r['cost'], ',.2f'):>10}  {models}")

    print(f"{'Start':<17}{'Hrs':>6}{'Proj':>8}  {'Session':<10}{'Cost':>10}  Models")
    print("-" * 78)
    table(billable)
    print("-" * 78)
    total = sum(r["cost"] for r in billable)
    hours = sum(r["hours"] for r in billable)
    print(f"{len(billable)} session(s){hours:>11.1f} h{'$' + format(total, ',.2f'):>29}"
          f"   (+20%: ${total * 1.2:,.2f})")

    if mixed:
        print()
        print(f"MIXED -- under {args.min_share:.0%} of the session was in this project.")
        print("Not counted above. Judge by hand whether any of it belongs to Caleb.")
        print("-" * 78)
        table(mixed)

    print()
    print("Proj = share of the session's recorded working directories inside this repo.")
    print("Hrs  = transcript wall clock, first event to last: an upper bound on attended")
    print("       time, and the input the VALUATION.md labour line uses.")


if __name__ == "__main__":
    main()

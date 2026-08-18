import json
from pathlib import Path
import re
from fastapi import APIRouter, HTTPException
from app.eventlog import recent_events
from app.vehicle_manager import LOG_DIR

router = APIRouter()

# Only ever touch files that match our own naming — no path traversal.
# \Z, not $: $ also matches before a trailing newline (%0A-suffixed names).
NAME_RE = re.compile(r"^flight_\d{8}_\d{6}\.jsonl\Z")

# NOTE: handlers are plain `def` (threadpool), NOT `async def`: parsing a big
# flight log is blocking file I/O and must never run on the event loop, where
# it would stall the telemetry WebSocket and every other endpoint.


def _read_log(path):
    meta, samples = {}, []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue  # a valid-JSON scalar/array line is still garbage
            if obj.get("meta"):
                meta = obj
            else:
                samples.append(obj)
    return meta, samples


def _duration(samples):
    """Last sample's t, tolerating a truncated final record (crash mid-write
    — exactly the log you most want to read)."""
    return samples[-1].get("t", 0) if samples else 0


@router.get("")
def list_logs():
    if not LOG_DIR.exists():
        return {"logs": []}
    out = []
    for p in sorted(LOG_DIR.glob("flight_*.jsonl"), reverse=True):
        try:
            meta, samples = _read_log(p)
            out.append({
                "name": p.name,
                "started": meta.get("started"),
                "duration": _duration(samples),
                "samples": len(samples),
                "size_kb": round(p.stat().st_size / 1024, 1),
                # Presence only — the list view shouldn't parse every card.
                "has_scorecard": p.with_suffix(".scorecard.json").exists(),
            })
        except Exception:
            continue
    return {"logs": out}


# NOTE: must be declared before /{name} or it would be captured as a log name.
@router.get("/events")
def get_events(limit: int = 100):
    """Recent structured ops events (connection, commands+ACKs, params,
    mode/arm changes, STATUSTEXT) — newest first. Full history is on disk in
    logs/events/events_YYYYMMDD.jsonl."""
    return {"events": recent_events(min(max(limit, 1), 500))}


def _scorecard_for(path: Path) -> dict | None:
    """The post-flight scorecard written alongside a flight log, if any.

    Absent for flights recorded before scorecards existed, and for a flight
    the backend never saw disarm (a crash, a killed process) — so callers must
    treat None as "not available", never as "nothing to report".
    """
    card = path.with_suffix(".scorecard.json")
    if not card.exists():
        return None
    try:
        return json.loads(card.read_text(encoding="utf-8"))
    except Exception:
        return None


@router.get("/{name}")
def get_log(name: str):
    if not NAME_RE.match(name):
        raise HTTPException(400, "Invalid log name")
    path = LOG_DIR / name
    if not path.exists():
        raise HTTPException(404, "Log not found")
    meta, samples = _read_log(path)
    return {"name": name, "meta": meta, "samples": samples,
            "duration": _duration(samples),
            "scorecard": _scorecard_for(path)}

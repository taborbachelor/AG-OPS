import json
import re
from fastapi import APIRouter, HTTPException
from app.eventlog import recent_events
from app.vehicle_manager import LOG_DIR

router = APIRouter()

# Only ever touch files that match our own naming — no path traversal.
NAME_RE = re.compile(r"^flight_\d{8}_\d{6}\.jsonl$")


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
            if obj.get("meta"):
                meta = obj
            else:
                samples.append(obj)
    return meta, samples


@router.get("")
async def list_logs():
    if not LOG_DIR.exists():
        return {"logs": []}
    out = []
    for p in sorted(LOG_DIR.glob("flight_*.jsonl"), reverse=True):
        try:
            meta, samples = _read_log(p)
            duration = samples[-1]["t"] if samples else 0
            out.append({
                "name": p.name,
                "started": meta.get("started"),
                "duration": duration,
                "samples": len(samples),
                "size_kb": round(p.stat().st_size / 1024, 1),
            })
        except Exception:
            continue
    return {"logs": out}


# NOTE: must be declared before /{name} or it would be captured as a log name.
@router.get("/events")
async def get_events(limit: int = 100):
    """Recent structured ops events (connection, commands+ACKs, params,
    mode/arm changes, STATUSTEXT) — newest first. Full history is on disk in
    logs/events/events_YYYYMMDD.jsonl."""
    return {"events": recent_events(min(max(limit, 1), 500))}


@router.get("/{name}")
async def get_log(name: str):
    if not NAME_RE.match(name):
        raise HTTPException(400, "Invalid log name")
    path = LOG_DIR / name
    if not path.exists():
        raise HTTPException(404, "Log not found")
    meta, samples = _read_log(path)
    return {"name": name, "meta": meta, "samples": samples,
            "duration": samples[-1]["t"] if samples else 0}

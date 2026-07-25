"""Structured JSON event logging (ops log, distinct from per-flight telemetry logs).

Every operationally significant event — connection changes, commands and their
ACK results, parameter writes, mode changes, arm/disarm, failsafe/STATUSTEXT
traffic, exceptions — goes through log_event(). Directive requirement: a bench
anomaly must be diagnosable from logs after the fact.

Output:
  - JSONL file per day: logs/events/events_YYYYMMDD.jsonl
    {"ts": "...", "level": "INFO", "component": "link", "event": "connected", ...fields}
  - Mirrored to the std "gcs.events" logger (visible in the uvicorn console).
  - In-memory ring of the last 500 events for the /api/logs/events endpoint.

Never raises: logging must not be able to take down a flight-critical path.
Thread-safe: called from the telemetry thread, reconnect thread, and API workers.
"""
import json
import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

EVENT_DIR = Path(__file__).resolve().parent.parent / "logs" / "events"

_logger = logging.getLogger("gcs.events")

_lock = threading.Lock()
_ring: deque = deque(maxlen=500)
_fh = None
_fh_day = None  # "YYYYMMDD" the open file belongs to (daily roll)


def _file_for_today():
    """Return an open handle for today's event file, rolling at midnight."""
    global _fh, _fh_day
    day = datetime.now().strftime("%Y%m%d")
    if _fh is not None and _fh_day == day:
        return _fh
    if _fh is not None:
        try:
            _fh.close()
        except Exception:
            pass
    EVENT_DIR.mkdir(parents=True, exist_ok=True)
    _fh = open(EVENT_DIR / f"events_{day}.jsonl", "a", encoding="utf-8")
    _fh_day = day
    return _fh


def log_event(component: str, event: str, level: str = "INFO", **fields):
    """Record a structured event. `fields` must be JSON-serializable (values
    that aren't are stringified rather than dropped)."""
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "t": round(time.time(), 3),
        "level": level,
        "component": component,
        "event": event,
    }
    for k, v in fields.items():
        try:
            json.dumps(v)
            rec[k] = v
        except (TypeError, ValueError):
            rec[k] = repr(v)
    try:
        with _lock:
            _ring.append(rec)
            fh = _file_for_today()
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
    except Exception:
        pass  # logging must never take down the caller
    try:
        line = f"{component}: {event} " + " ".join(f"{k}={v}" for k, v in fields.items())
        _logger.log(getattr(logging, level, logging.INFO)
                    if level != "WARN" else logging.WARNING, line)
    except Exception:
        pass


def recent_events(limit: int = 100) -> list:
    """Most recent events, newest first."""
    with _lock:
        items = list(_ring)
    return list(reversed(items))[:max(0, limit)]

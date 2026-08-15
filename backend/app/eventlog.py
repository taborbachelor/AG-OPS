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

# Two locks: _lock guards the in-memory ring (taken by the API's /events
# reader too, so it must never be held across disk I/O — a stalled disk would
# otherwise freeze the event loop); _io_lock serializes the file writes.
_lock = threading.Lock()
_io_lock = threading.Lock()
_ring: deque = deque(maxlen=500)
_fh = None
_fh_day = None  # "YYYYMMDD" the open file belongs to (daily roll)

# Record fields owned by log_event itself; a caller-supplied field with one of
# these names is stored under an "f_" prefix instead of clobbering the record.
_CORE_FIELDS = frozenset({"ts", "t", "level", "component", "event"})


def _file_for_today():
    """Return an open handle for today's event file, rolling at midnight.
    Uses the UTC day so file names match the records' UTC `ts` values."""
    global _fh, _fh_day
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
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
    # Field sanitization must be as unraisable as the write below: json.dumps
    # can raise RecursionError (not just TypeError/ValueError) on deep values,
    # and a broken __repr__ raises out of the fallback — either would kill a
    # flight-critical caller (telemetry/guardian/reconnect thread).
    try:
        for k, v in fields.items():
            if k in _CORE_FIELDS:
                k = "f_" + k
            try:
                # allow_nan=False: a NaN/inf telemetry float would otherwise
                # write a bare `NaN` literal that strict parsers reject.
                json.dumps(v, allow_nan=False)
                rec[k] = v
            except Exception:
                try:
                    rec[k] = repr(v)
                except Exception:
                    rec[k] = "<unrepresentable>"
    except Exception:
        pass
    try:
        with _lock:
            _ring.append(rec)
        # Disk I/O outside the ring lock: the /events reader (event loop!)
        # takes _lock, and must never wait behind a slow flush.
        with _io_lock:
            fh = _file_for_today()
            fh.write(json.dumps(rec, default=repr) + "\n")
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

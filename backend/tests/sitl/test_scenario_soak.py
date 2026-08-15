"""Scenario: soak — a real spray job flown while the UI hammers the backend.

The endurance question behind "the UI must run cleanly": does the backend
stay responsive and truthful for a whole autonomous mission while WebSocket
telemetry streams and API pollers load it the way the real GCS does?

Asserts, over the entire flight:
  - telemetry WS frames keep flowing (no stall > 5s wall),
  - the link never goes LOST,
  - the mission (planned by the real coverage engine) completes,
  - zero unhandled exceptions / guardian tick errors in the event log,
  - process memory growth stays bounded.
"""
import ctypes
import sys
import threading
import time

import pytest

from tests.sitl import harness as h

pytestmark = [pytest.mark.sitl, pytest.mark.slow]

SPEEDUP = 5.0


def _rss_mb() -> float:
    """Working-set of this process (backend runs in-process), Windows API."""
    if sys.platform != "win32":
        return 0.0

    class PMC(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_uint32), ("PageFaultCount", ctypes.c_uint32),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]

    pmc = PMC()
    pmc.cb = ctypes.sizeof(PMC)
    ctypes.windll.psapi.GetProcessMemoryInfo(
        ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
    return pmc.WorkingSetSize / (1024 * 1024)


class _Load:
    """UI-shaped load: N WS telemetry readers + API pollers, all watching for
    trouble (frame gaps, LOST link, request failures) while they run."""

    def __init__(self, client):
        self.client = client
        self.stop = threading.Event()
        self.max_ws_gap = 0.0
        self.ws_frames = 0
        self.poll_errors = []
        self.saw_lost = False
        self.threads = []

    def _ws_reader(self):
        with self.client.websocket_connect("/api/telemetry/ws") as ws:
            last = time.monotonic()
            while not self.stop.is_set():
                ws.receive_json()
                now = time.monotonic()
                self.max_ws_gap = max(self.max_ws_gap, now - last)
                last = now
                self.ws_frames += 1

    def _poller(self, paths, hz):
        while not self.stop.is_set():
            for p in paths:
                try:
                    r = self.client.get(p)
                    if r.status_code != 200:
                        self.poll_errors.append(f"{p} -> {r.status_code}")
                    elif p == "/api/telemetry/" and r.json().get("link_state") == "LOST":
                        self.saw_lost = True
                except Exception as e:  # noqa: BLE001 — record, keep loading
                    self.poll_errors.append(f"{p}: {e}")
            time.sleep(1.0 / hz)

    def __enter__(self):
        specs = [
            (self._ws_reader, ()), (self._ws_reader, ()),
            (self._poller, (["/api/telemetry/", "/api/connection/status"], 5)),
            (self._poller, (["/api/logs/events?limit=50",
                             "/api/vehicle/params?cached=true",
                             "/api/safety/guardian"], 2)),
        ]
        for fn, args in specs:
            t = threading.Thread(target=fn, args=args, daemon=True)
            t.start()
            self.threads.append(t)
        return self

    def __exit__(self, *exc):
        self.stop.set()
        for t in self.threads:
            t.join(timeout=5)


def test_full_spray_mission_under_ui_load(client):
    ready = h.launch(client, speedup=SPEEDUP, fresh_eeprom=True)
    lat = ready.get("home_lat") or ready["lat"]
    lon = ready.get("home_lon") or ready["lon"]

    # A real job from the real planner: ~800x400m field, 40m swath.
    import math
    dl = 400.0 / 111320.0
    dn = 800.0 / (111320.0 * math.cos(math.radians(lat)))
    polygon = [{"lat": lat + 0.001, "lon": lon - dn / 2},
               {"lat": lat + 0.001, "lon": lon + dn / 2},
               {"lat": lat + 0.001 + dl, "lon": lon + dn / 2},
               {"lat": lat + 0.001 + dl, "lon": lon - dn / 2}]
    r = client.post("/api/coverage/plan",
                    json={"polygon": polygon, "swath": 40, "alt": 50})
    assert r.status_code == 200, r.text
    plan_wps = r.json()["waypoints"]
    assert len(plan_wps) >= 10, f"planner produced too few waypoints: {len(plan_wps)}"

    items = ([{"command": "TAKEOFF", "lat": lat, "lon": lon, "alt": 40}]
             + [{"command": "WAYPOINT", "lat": w["lat"], "lon": w["lon"],
                 "alt": w["alt"]} for w in plan_wps]
             + [{"command": "RTL", "lat": lat, "lon": lon, "alt": 0}])
    h.upload_mission(client, items)
    total = len(items) + 1  # + home item at seq 0

    rss_start = _rss_mb()
    with _Load(client) as load:
        h.start_mission(client)
        h.force_arm(client)
        h.wait_for(client, lambda t: t["armed"], 30, "armed")
        h.wait_for(client, lambda t: t["altitude"] > 30, 120, "airborne")
        # The whole spray pattern, under load, to the final RTL item.
        h.wait_for(client, lambda t: t["mission_seq"] >= total - 1, 480,
                   f"mission complete (seq {total - 1})", interval=2.0)
        h.wait_for(client, lambda t: h.dist_home_m(t) < 400, 240,
                   "returned home under load")
    rss_end = _rss_mb()

    # The backend never flinched.
    assert load.ws_frames > 500, f"WS barely streamed: {load.ws_frames} frames"
    assert load.max_ws_gap < 5.0, \
        f"telemetry stalled for {load.max_ws_gap:.1f}s during the mission"
    assert not load.saw_lost, "link went LOST mid-mission"
    assert not load.poll_errors, f"API errors under load: {load.poll_errors[:5]}"

    events = h.recent_events(client, 500)
    assert not h.has_event(events, "core", "unhandled_exception"), \
        "unhandled exception during the soak"
    assert not h.has_event(events, "guardian", "tick_error")

    if sys.platform == "win32":
        growth = rss_end - rss_start
        assert growth < 150, f"memory grew {growth:.0f}MB over one mission"

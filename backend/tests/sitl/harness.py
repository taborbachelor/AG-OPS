"""Shared helpers for the M4 SITL scenario harness.

Every scenario drives the REAL FastAPI app in-process (TestClient) against the
REAL bundled ArduPlane SITL — the same stack an operator uses, minus uvicorn.
Nothing here talks MAVLink directly: if a scenario can't be expressed through
the public API, that's an API gap to fix, not a reason to bypass it.

Timing rules learned the hard way (see CLAUDE-CALEB.md dev-loop notes):
  - This SITL build EXITS when its TCP client disconnects. Each scenario gets
    a fresh spawn; teardown disconnect naturally kills it.
  - GPS/EKF convergence is ~30 SIM seconds after boot. At speedup N that's
    ~30/N wall seconds. wait_flight_ready() budgets generously.
  - Our GCS heartbeat is 1 Hz WALL time. At speedup N the vehicle sees a
    heartbeat every N SIM seconds — so any scenario that enables the GCS
    failsafe (FS_GCS_ENABL) must run at speedup 1, or the vehicle would RTL
    spuriously mid-test. Scenarios that run fast keep FS_GCS_ENABL=0.
  - The SITL port is NOT hardcoded here. The backend spawns SITL, so the backend
    picks the port (app.config.SITL_INSTANCE) and we ask it — see sitl_conn().
    Set SITL_INSTANCE=<n> to run a second suite on the same machine without the
    two colliding; conftest.py enforces that the port is genuinely ours.
"""
import math
import time

from app.config import SITL_MAX_INSTANCE
from app.routers import sim

# Sabetha, KS — must match sim.SITL_HOME.
HOME_LAT, HOME_LON = 39.9042, -95.7997

# Generous: a SITL told to die frees the port in well under a second, so anything
# approaching this bound means something else owns it.
PORT_FREE_TIMEOUT = 20.0

# Telemetry keys worth showing when a wait times out (full snapshots are noisy).
_DEBUG_KEYS = ("armed", "mode", "altitude", "groundspeed", "gps_fix",
               "gps_satellites", "ekf_healthy", "battery_voltage",
               "mission_seq", "mission_count", "wp_dist", "link_state",
               "connected", "reconnecting", "gcs_hb_suppressed", "statustext")


def telem(client) -> dict:
    r = client.get("/api/telemetry/")
    assert r.status_code == 200, f"telemetry endpoint failed: {r.status_code} {r.text}"
    return r.json()


def _trim(snapshot: dict) -> dict:
    return {k: snapshot.get(k) for k in _DEBUG_KEYS}


def wait_for(client, pred, timeout: float, what: str, interval: float = 0.5) -> dict:
    """Poll telemetry until pred(snapshot) is truthy. Raises with a trimmed
    last-snapshot dump on timeout so a red scenario is diagnosable from CI."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = telem(client)
        if pred(last):
            return last
        time.sleep(interval)
    raise AssertionError(
        f"timed out after {timeout:.0f}s waiting for: {what}\n"
        f"last telemetry: {_trim(last) if last else None}")


def start_sim(client, speedup: float = 1.0, fresh_eeprom: bool = True) -> dict:
    r = client.post("/api/sim/start",
                    json={"speedup": speedup, "fresh_eeprom": fresh_eeprom})
    assert r.status_code == 200, f"sim start failed: {r.status_code} {r.text}"
    return r.json()


def stop_sim(client):
    client.post("/api/sim/stop")


def port_contention_message() -> str:
    """Why a scenario refused to start, in the operator's terms.

    Worth its own function because the whole point of the setup guard is that
    the message names contention explicitly — the failure it replaces was a bare
    WinError 10061 mid-scenario, which reads like a regression in the code under
    test and cost real time being chased as one.
    """
    return (
        f"TCP {sim.SITL_PORT} was still LISTENING {PORT_FREE_TIMEOUT:.0f}s into "
        f"setup, so this scenario never ran.\n"
        f"Something else owns SITL instance {sim.SITL_INSTANCE}: a hand-started "
        f"run_sitl.bat, a SITL leaked by an earlier scenario, or a parallel test "
        f"session.\n"
        f"This is a hard stop rather than a silent reuse — scenarios need their "
        f"own SITL (fresh eeprom, scenario speedup), and adopting a stranger's "
        f"would quietly test the wrong vehicle.\n"
        f"Fix: close the other SITL, or give this run its own port with "
        f"SITL_INSTANCE=<n> (0-{SITL_MAX_INSTANCE}).")


def port_is_free(timeout: float = PORT_FREE_TIMEOUT,
                 port: int | None = None) -> bool:
    """True once this instance's SITL port is free. See sim.wait_port_free."""
    return sim.wait_port_free(timeout, port=port)


def sitl_conn(client) -> str:
    """Where this process's SITL listens, straight from the backend that spawns
    it. Asked fresh rather than cached in a module constant: the port is the
    backend's to choose, and a second copy here is precisely how the two end up
    disagreeing about which simulator a scenario is talking to."""
    r = client.get("/api/sim/status")
    assert r.status_code == 200, f"sim status failed: {r.status_code} {r.text}"
    conn = r.json().get("connection_string")
    assert conn, f"sim status reported no connection_string: {r.text}"
    return conn


def connect(client) -> dict:
    r = client.post("/api/connection/connect",
                    json={"connection_string": sitl_conn(client), "baud": 115200})
    assert r.status_code == 200, f"connect failed: {r.status_code} {r.text}"
    return r.json()


def disconnect(client):
    client.post("/api/connection/disconnect")


def launch(client, speedup: float = 1.0, fresh_eeprom: bool = True,
           ready_timeout: float = 120.0) -> dict:
    """Spawn SITL, connect the GCS link, and wait until the vehicle is actually
    flight-ready (3D fix + healthy EKF + home known). Returns the ready
    snapshot. This is the standard scenario preamble."""
    start_sim(client, speedup=speedup, fresh_eeprom=fresh_eeprom)
    connect(client)
    return wait_flight_ready(client, timeout=ready_timeout)


def wait_flight_ready(client, timeout: float = 120.0) -> dict:
    return wait_for(
        client,
        lambda t: (t.get("gps_fix", 0) >= 3 and t.get("ekf_healthy")
                   and (t.get("home_lat") or t.get("lat"))),
        timeout, "GPS 3D fix + EKF healthy + position known")


def inject_fault(client, fault: str, enable: bool = True, value=None) -> dict:
    body = {"fault": fault, "enable": enable}
    if value is not None:
        body["value"] = value
    r = client.post("/api/sim/fault", json=body)
    assert r.status_code == 200, f"fault injection failed: {r.status_code} {r.text}"
    return r.json()


def guardian(client) -> dict:
    """Guardian block from telemetry: {state, monitors, warnings, rtl_*}."""
    return telem(client).get("guardian") or {}


def wait_warning(client, needle: str, timeout: float, what: str = "") -> dict:
    """Wait until a guardian warning containing `needle` is present.

    Returns the telemetry snapshot that carried it. Matching on the warning
    TEXT is deliberate: the operator-facing string is the product surface —
    if it stops naming the condition, the scenario should fail.
    """
    needle_l = needle.lower()
    return wait_for(
        client,
        lambda t: any(needle_l in w.lower()
                      for w in ((t.get("guardian") or {}).get("warnings") or [])),
        timeout, what or f"guardian warning containing {needle!r}",
        interval=0.3)


def set_failsafe(client, **overrides) -> dict:
    """Apply failsafe params through the real safety endpoint (atomic, verified).
    Defaults are benign for fast (speedup>1) scenarios: GCS failsafe OFF so our
    wall-clock 1Hz heartbeat can't trip it in compressed sim time."""
    cfg = {
        "batt_low_volt": 10.5, "batt_low_action": 0,
        "batt_crit_volt": 9.0, "batt_crit_action": 0,
        "gcs_enable": 0, "rc_enable": True, "rc_long_action": 1,
    }
    cfg.update(overrides)
    r = client.post("/api/safety/failsafe", json=cfg)
    assert r.status_code == 200, f"failsafe apply failed: {r.status_code} {r.text}"
    return r.json()


def offset(lat: float, lon: float, north_m: float, east_m: float):
    """Simple equirectangular offset — plenty accurate at mission scale."""
    dlat = north_m / 111320.0
    dlon = east_m / (111320.0 * math.cos(math.radians(lat)))
    return lat + dlat, lon + dlon


def upload_mission(client, items: list[dict]) -> dict:
    r = client.post("/api/mission/upload", json={"items": items})
    assert r.status_code == 200, f"mission upload failed: {r.status_code} {r.text}"
    return r.json()


def force_arm(client) -> dict:
    r = client.post("/api/vehicle/arm", json={"force": True})
    assert r.status_code == 200, f"arm failed: {r.status_code} {r.text}"
    return r.json()


def start_mission(client):
    r = client.post("/api/mission/start")
    assert r.status_code == 200, f"mission start failed: {r.status_code} {r.text}"


def set_mode(client, mode: str, expect_ok: bool = True):
    r = client.post("/api/vehicle/mode", json={"mode": mode})
    if expect_ok:
        assert r.status_code == 200, f"mode {mode} failed: {r.status_code} {r.text}"
    return r


def takeoff(client, alt: float = 50.0) -> dict:
    r = client.post("/api/vehicle/takeoff", json={"alt": alt, "force": True})
    assert r.status_code == 200, f"takeoff failed: {r.status_code} {r.text}"
    return r.json()


def dist_home_m(t: dict) -> float:
    """Ground distance from current position to home, meters."""
    lat, lon = t.get("lat", 0.0), t.get("lon", 0.0)
    hlat = t.get("home_lat") or HOME_LAT
    hlon = t.get("home_lon") or HOME_LON
    dn = (lat - hlat) * 111320.0
    de = (lon - hlon) * 111320.0 * math.cos(math.radians(hlat))
    return math.hypot(dn, de)


def recent_events(client, limit: int = 200) -> list[dict]:
    r = client.get(f"/api/logs/events?limit={limit}")
    assert r.status_code == 200
    body = r.json()
    return body.get("events", body) if isinstance(body, dict) else body


def has_event(events: list[dict], component: str, name: str) -> bool:
    return any(e.get("component") == component and e.get("event") == name
               for e in events)

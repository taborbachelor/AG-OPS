"""Runtime configuration, env-var backed.

M2 introduces the first real config surface (the gap analysis flagged config
management as absent). Keep it small and explicit; grow it as later milestones
need settings. Every value has a safe default so nothing must be set to run.
"""
import os

from pymavlink import mavutil


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _bool_env(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


# --- Our GCS identity on the MAVLink network ---
# Default 252, deliberately NOT 255: Mission Planner defaults to system id 255,
# so sharing an endpoint with it collides (each GCS can consume the other's
# targeted ACKs / PARAM_VALUEs). A distinct id + RX sysid filtering keeps the
# two apart. ArduPilot's SYSID_MYGCS param gates which GCS its failsafe counts
# heartbeats from, so connect() aligns the vehicle to this id (see below).
GCS_SYSID = _int_env("GCS_SYSID", 252)
GCS_COMPID = _int_env(
    "GCS_COMPID", getattr(mavutil.mavlink, "MAV_COMP_ID_MISSIONPLANNER", 190))

# On connect, set the vehicle's "which GCS commands me" param to GCS_SYSID with a
# verified (M1b) write so its GCS-loss failsafe recognizes our heartbeats.
# Without this, moving off sysid 255 makes ArduPilot ignore our heartbeats and
# RTL on FS_GCS — the exact failure mode from the project history. Disable to
# leave vehicle config untouched (then the operator must set it themselves).
MANAGE_SYSID_MYGCS = _bool_env("MANAGE_SYSID_MYGCS", True)

# Telemetry stream rate (MAV_DATA_STREAM_ALL) requested on connect.
# 0 = automatic: 10 Hz on network links (TCP/UDP — SITL, wifi bridges),
# 4 Hz on serial links, where ALL-streams at 10 Hz can saturate a 57600-baud
# telemetry radio (dropped heartbeats, flapping link, failing transfers).
TELEM_RATE_HZ = _int_env("TELEM_RATE_HZ", 0)

# The parameter naming the vehicle's commanding GCS differs by firmware age:
# ArduPilot 4.5+ renamed SYSID_MYGCS -> MAV_GCS_SYSID. connect() aligns whichever
# one the vehicle actually has (tried in this order), so we work across versions.
GCS_SYSID_PARAM_CANDIDATES = ("MAV_GCS_SYSID", "SYSID_MYGCS")

# --- Built-in SITL ---
# Which SITL instance this process spawns and talks to. ArduPilot's -I N offsets
# EVERY port the simulator binds by 10*N — SERIAL0's TCP listener, RC-in, and the
# physics sockets — so two GCS+SITL pairs can share a machine without colliding on
# some port nobody thought to check. 0 keeps the historical 5760.
#
# This exists because the scenario suite is single-occupancy on 5760: two sessions
# running it at once fail a DIFFERENT set of scenarios each time, always at the
# connection level, never on an assertion. Give each run its own instance.
SITL_MAX_INSTANCE = 9


def _sitl_instance() -> int:
    """Read SITL_INSTANCE, loudly.

    Deliberately NOT _int_env: that falls back to the default on a bad value, and
    a typo'd instance silently meaning 0 would recreate the exact port collision
    this setting exists to prevent. A misconfigured run must not look like a
    working one.
    """
    raw = os.environ.get("SITL_INSTANCE")
    if raw is None or not raw.strip():
        return 0
    try:
        n = int(raw)
    except ValueError:
        raise ValueError(f"SITL_INSTANCE must be an integer 0..{SITL_MAX_INSTANCE}, "
                         f"got {raw!r}") from None
    if not 0 <= n <= SITL_MAX_INSTANCE:
        raise ValueError(f"SITL_INSTANCE must be 0..{SITL_MAX_INSTANCE}, got {n}")
    return n


SITL_INSTANCE = _sitl_instance()

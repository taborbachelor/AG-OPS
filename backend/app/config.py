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

# The parameter naming the vehicle's commanding GCS differs by firmware age:
# ArduPilot 4.5+ renamed SYSID_MYGCS -> MAV_GCS_SYSID. connect() aligns whichever
# one the vehicle actually has (tried in this order), so we work across versions.
GCS_SYSID_PARAM_CANDIDATES = ("MAV_GCS_SYSID", "SYSID_MYGCS")

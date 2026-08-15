"""Parameter metadata & validation (directive M3).

The full ArduPilot parameter metadata set (apm.pdef.xml) is thousands of
entries and megabytes per vehicle type — too heavy to bundle here. Instead we
validate on two pragmatic axes that catch the mistakes that actually matter:

  1. **Type fit** — an INTx param must get an integer within that int type's
     range (ArduPilot rides values on the wire as float32 and casts, so writing
     200 to an INT8 would silently wrap). Floats must be finite.
  2. **Curated ranges** — sane bounds for the parameters this GCS actually
     writes (fence, failsafes, GCS identity, waypoint radius). Extend freely.

Values outside these are rejected at the API before a PARAM_SET ever goes out.
Full metadata ingestion (per-firmware apm.pdef.xml) is a future enhancement.
"""
import math

from pymavlink import mavutil

# MAV_PARAM_TYPE -> (min, max) for the integer types, so we never send a value
# that would wrap when the FC casts it into that storage type.
_TYPE_RANGES = {
    mavutil.mavlink.MAV_PARAM_TYPE_UINT8: (0, 255),
    mavutil.mavlink.MAV_PARAM_TYPE_INT8: (-128, 127),
    mavutil.mavlink.MAV_PARAM_TYPE_UINT16: (0, 65535),
    mavutil.mavlink.MAV_PARAM_TYPE_INT16: (-32768, 32767),
    mavutil.mavlink.MAV_PARAM_TYPE_UINT32: (0, 4294967295),
    mavutil.mavlink.MAV_PARAM_TYPE_INT32: (-2147483648, 2147483647),
}
_INT_TYPES = frozenset(_TYPE_RANGES) | {
    mavutil.mavlink.MAV_PARAM_TYPE_UINT64, mavutil.mavlink.MAV_PARAM_TYPE_INT64}

# Curated (min, max) bounds for the parameters this GCS writes. Not exhaustive —
# a param absent here is validated only by type. Values are inclusive.
# Bounds are set no narrower than the UI's own pydantic constraints so we never
# reject a value the operator was allowed to pick — they exist to stop gross
# nonsense (negatives, absurd magnitudes) reaching the FC, especially via the
# raw /vehicle/params endpoint which has no schema.
PARAM_RANGES = {
    # Geofence (SafetyPanel — pydantic allows radius<=50000, alt<=10000)
    "FENCE_ENABLE": (0, 1),
    "FENCE_TYPE": (0, 15),
    "FENCE_ACTION": (0, 6),
    "FENCE_RADIUS": (1, 50000),
    "FENCE_ALT_MAX": (1, 10000),
    # Failsafes (SafetyPanel)
    "BATT_LOW_VOLT": (0, 100),
    "BATT_CRT_VOLT": (0, 100),
    "BATT_FS_LOW_ACT": (0, 7),
    "BATT_FS_CRT_ACT": (0, 7),
    "FS_GCS_ENABL": (0, 2),
    "THR_FAILSAFE": (0, 2),
    "FS_LONG_ACTN": (0, 4),
    # Navigation / identity
    "WP_RADIUS": (1, 32767),
    "MAV_GCS_SYSID": (1, 255),
    "SYSID_MYGCS": (1, 255),
    # SITL fault injection (sim router, M4) — bounded so a typo'd injection
    # can't write nonsense to a real vehicle that happens to have SIM_* params.
    "SIM_GPS_DISABLE": (0, 1),
    "SIM_GPS1_ENABLE": (0, 1),
    "SIM_BATT_VOLTAGE": (0, 100),
    # First-flight bundle (bench router). Both param generations covered:
    # older firmware has ARMING_CHECK/ALT_HOLD_RTL(cm), newer (4.8+) has
    # ARMING_SKIPCHK/ARMING_REQUIRE/RTL_ALTITUDE(m).
    "FS_SHORT_ACTN": (0, 4),
    "ALT_HOLD_RTL": (-1, 100000),   # cm; -1 = return at current altitude
    "ARMING_CHECK": (0, 1 << 20),   # bitmask; 1 = all checks
    "ARMING_SKIPCHK": (0, 1 << 20),  # bitmask; 0 = skip nothing
    "ARMING_REQUIRE": (0, 2),
    "RTL_ALTITUDE": (1, 1000),      # m
}
# Servo exerciser (bench router) parks/restores output functions.
PARAM_RANGES.update({f"SERVO{i}_FUNCTION": (0, 300) for i in range(1, 17)})


def validate(name: str, value, param_type=None):
    """Validate a proposed parameter write.

    Returns (ok: bool, error: str|None). `param_type` is the vehicle's real
    MAV_PARAM_TYPE from the cache when known (enables the int-type checks); pass
    None to skip type validation and check curated ranges only.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False, "value is not a number"
    if not math.isfinite(v):
        return False, "value must be finite (not NaN/inf)"

    if param_type in _INT_TYPES:
        if abs(v - round(v)) > 1e-6:
            return False, f"{name} is an integer parameter; {v} is not an integer"
        lo_hi = _TYPE_RANGES.get(param_type)
        if lo_hi and not (lo_hi[0] <= v <= lo_hi[1]):
            return False, f"{name}={int(v)} is outside its storage range {lo_hi}"

    rng = PARAM_RANGES.get(name)
    if rng and not (rng[0] <= v <= rng[1]):
        return False, f"{name}={v} is outside the allowed range {rng[0]}..{rng[1]}"

    return True, None

"""Server-side pre-flight gate (directive M6: safety logic in the backend).

The UI has always rendered a checklist, but the backend would arm regardless —
for an autonomous aircraft the go/no-go authority must live where the command
is executed. evaluate() is pure logic over a telemetry snapshot (+ the cached
fence param), the same discipline as guardian.py. The arm/takeoff endpoints
refuse while a BLOCKER fails unless the operator explicitly overrides.

Blockers (no-go): link READY, GPS 3D fix, EKF healthy, home/position known.
Advisories (go, but told): battery, RC input seen, geofence enabled, sensors
healthy. Advisories never block — a bench pilot must be able to fly with the
fence off, but never without knowing.
"""

BLOCKERS = ("link", "gps", "ekf", "home")


def _check(cid, label, ok, blocker, detail=""):
    return {"id": cid, "label": label, "ok": bool(ok),
            "blocker": blocker, "detail": detail}


def evaluate(t: dict, fence_enable=None) -> dict:
    """Judge flight readiness from a snapshot() dict. `fence_enable` is the
    cached FENCE_ENABLE value (None = unknown/not synced yet)."""
    checks = []

    connected = bool(t.get("connected"))
    state = t.get("link_state")
    checks.append(_check(
        "link", "Link READY",
        connected and state == "READY",
        True, f"state={state}" if connected else "not connected"))

    fix = t.get("gps_fix", 0)
    sats = t.get("gps_satellites", 0)
    checks.append(_check("gps", "GPS 3D fix", fix >= 3,
                         True, f"fix={fix}, sats={sats}"))

    checks.append(_check("ekf", "EKF healthy", bool(t.get("ekf_healthy")),
                         True, f"flags={t.get('ekf_flags', 0)}"))

    has_home = bool(t.get("home_lat")) or bool(t.get("lat"))
    checks.append(_check("home", "Home position known", has_home, True))

    volts = t.get("battery_voltage") or 0.0
    level = t.get("battery_level")
    batt_ok = (level is None or level > 30) and volts > 0
    checks.append(_check(
        "battery", "Battery", batt_ok, False,
        f"{volts:.1f}V" + (f", {level}%" if level is not None else "")))

    rc = t.get("rc_channels") or []
    rc_ok = any(isinstance(v, (int, float)) and 900 <= v <= 2100 for v in rc)
    checks.append(_check("rc", "RC input seen", rc_ok, False,
                         f"{len(rc)} channels"))

    fence_ok = fence_enable is not None and float(fence_enable) >= 1
    checks.append(_check(
        "fence", "Geofence enabled", fence_ok, False,
        "unknown (params not synced)" if fence_enable is None
        else f"FENCE_ENABLE={int(float(fence_enable))}"))

    errors = t.get("sensor_errors") or []
    checks.append(_check("sensors", "Sensors healthy", not errors, False,
                         ", ".join(errors)))

    failed_blockers = [c["id"] for c in checks if c["blocker"] and not c["ok"]]
    return {"ready": not failed_blockers,
            "failed_blockers": failed_blockers,
            "advisories_failing": [c["id"] for c in checks
                                   if not c["blocker"] and not c["ok"]],
            "checks": checks}

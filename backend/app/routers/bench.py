"""Bench & first-flight setup kit (/api/bench).

The Mission-Planner-style setup surface this GCS never had — built for the day
the internals go into the printed airframe:

  - servo exerciser (DO_SET_SERVO) with a prop-off guarantee: any channel
    whose SERVO<n>_FUNCTION is Throttle is refused unless the operator passes
    allow_throttle=true — a bench battery + an accidental "servo 3 sweep"
    must never spin the prop by surprise.
  - calibration commands (gyro / board level / simple accel / baro, compass
    cal start/accept/cancel), ack-checked; progress arrives as STATUSTEXT in
    the existing telemetry ring.
  - first-flight parameter bundle: operations/safety params ONLY (failsafes,
    fence, RTL altitude, arming checks) parameterized by battery cell count —
    deliberately NO tuning/airframe params (SERVOn_FUNCTION, TECS, ...): those
    depend on the physical build and are set per-airframe, not by a bundle.
    Applied via set_params_atomic (all-or-nothing + rollback), preview first.

Everything here is DISARMED-only: this is a bench, not a cockpit.
"""
import time
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from pymavlink import mavutil

from app.eventlog import log_event
from app.vehicle_manager import vehicle_manager

router = APIRouter()

_CAL = {
    # kind -> (p1..p7) for MAV_CMD_PREFLIGHT_CALIBRATION
    "gyro": (1, 0, 0, 0, 0, 0, 0),
    "baro": (0, 0, 1, 0, 0, 0, 0),
    "level": (0, 0, 0, 0, 2, 0, 0),         # board level
    "accel_simple": (0, 0, 0, 0, 4, 0, 0),  # simple accel cal
}


def _require_bench():
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    if vehicle_manager.snapshot().get("armed"):
        raise HTTPException(409, "Bench operations are DISARMED-only")


def _raise_if_failed(res: dict, what: str):
    if not res.get("ok"):
        raise HTTPException(502, f"{what} failed: {res.get('error')}")


# Two exercisers, matching how ArduPilot actually works (proven on the
# harness — DO_SET_SERVO returns result=4 on any function-assigned channel,
# and runtime-parking the function doesn't take effect without a reboot):
#   /servo   — AUX channels only (SERVOn_FUNCTION=0: spray pump, drop
#              mechanism, ...) via DO_SET_SERVO.
#   /surface — control surfaces via the REAL pilot path: MANUAL mode + an
#              RC override held for a few seconds, then always released.
#              Exercises the same passthrough the RadioMaster uses.

class ServoTest(BaseModel):
    channel: int = Field(ge=1, le=16)
    pwm: int = Field(ge=800, le=2200)


@router.post("/servo")
def servo_test(req: ServoTest):
    """Drive an UNASSIGNED (aux) servo output — payload/pump channels. For
    ailerons/elevator/rudder/throttle use /surface (the firmware refuses
    DO_SET_SERVO on function-assigned channels)."""
    _require_bench()
    func = vehicle_manager.cached_value(f"SERVO{req.channel}_FUNCTION")
    if func is not None and int(float(func)) != 0:
        raise HTTPException(409, {
            "message": f"SERVO{req.channel} has function {int(float(func))} "
                       f"assigned — the autopilot owns it",
            "hint": "control surfaces are exercised via POST /api/bench/surface",
        })
    res = vehicle_manager.run_command(
        mavutil.mavlink.MAV_CMD_DO_SET_SERVO, req.channel, req.pwm,
        name="servo_test")
    _raise_if_failed(res, f"servo {req.channel} -> {req.pwm}")
    return {"status": "ok", "channel": req.channel, "pwm": req.pwm}


class ServoRelease(BaseModel):
    channel: int = Field(ge=1, le=16)


@router.post("/servo/release")
def servo_release(req: ServoRelease):
    """Return an aux servo to autopilot control (DO_SET_SERVO pwm=0)."""
    _require_bench()
    res = vehicle_manager.run_command(
        mavutil.mavlink.MAV_CMD_DO_SET_SERVO, req.channel, 0,
        name="servo_release")
    _raise_if_failed(res, f"servo {req.channel} release")
    return {"status": "ok", "channel": req.channel}


_RCMAP = {"aileron": ("RCMAP_ROLL", 1), "elevator": ("RCMAP_PITCH", 2),
          "throttle": ("RCMAP_THROT", 3), "rudder": ("RCMAP_YAW", 4)}


class SurfaceTest(BaseModel):
    surface: Literal["aileron", "elevator", "rudder", "throttle"]
    pwm: int = Field(ge=900, le=2100)
    hold_s: float = Field(3.0, ge=0.5, le=10)
    allow_throttle: bool = False


@router.post("/surface")
def surface_test(req: SurfaceTest):
    """Deflect a control surface through the real pilot path: MANUAL mode +
    RC override streamed for hold_s seconds, then released. The response
    reports SERVO_OUTPUT_RAW sampled during the hold so 'did the surface
    move?' has a telemetry answer, not just an eyeball one. Synchronous:
    returns after the hold. PROP OFF unless you know why you're here."""
    _require_bench()
    if req.surface == "throttle" and not req.allow_throttle:
        raise HTTPException(409, {
            "message": "throttle test refused without allow_throttle=true",
            "hint": "remove the prop, then pass allow_throttle=true",
        })
    rcmap_name, default_ch = _RCMAP[req.surface]
    cached = vehicle_manager.cached_value(rcmap_name)
    rc_ch = int(float(cached)) if cached is not None else default_ch
    if not vehicle_manager.set_mode("MANUAL"):
        raise HTTPException(502, "vehicle refused MANUAL mode")
    channels = [0] * 8
    channels[rc_ch - 1] = req.pwm
    log_event("bench", "surface_test", surface=req.surface, rc_channel=rc_ch,
              pwm=req.pwm, hold_s=req.hold_s)
    sampled = None
    try:
        deadline = time.monotonic() + req.hold_s
        while time.monotonic() < deadline:
            if not vehicle_manager.send_rc_override(channels):
                raise HTTPException(502, "RC override send failed (link lost?)")
            time.sleep(0.05)  # ~20Hz, or ArduPilot times the override out
            sampled = vehicle_manager.snapshot().get("servo_outputs")
    finally:
        vehicle_manager.release_rc_override()
    return {"status": "ok", "surface": req.surface, "rc_channel": rc_ch,
            "pwm": req.pwm, "servo_outputs_during_hold": sampled}


class CalibrateRequest(BaseModel):
    kind: Literal["gyro", "baro", "level", "accel_simple",
                  "mag_start", "mag_accept", "mag_cancel"]


@router.post("/calibrate")
def calibrate(req: CalibrateRequest):
    """Run a calibration. Keep the aircraft still for gyro/level/accel_simple;
    rotate it through all axes after mag_start (progress lands in STATUSTEXT)."""
    _require_bench()
    if req.kind in _CAL:
        res = vehicle_manager.run_command(
            mavutil.mavlink.MAV_CMD_PREFLIGHT_CALIBRATION, *_CAL[req.kind],
            name=f"calibrate_{req.kind}")
    else:
        cmd = {"mag_start": mavutil.mavlink.MAV_CMD_DO_START_MAG_CAL,
               "mag_accept": mavutil.mavlink.MAV_CMD_DO_ACCEPT_MAG_CAL,
               "mag_cancel": mavutil.mavlink.MAV_CMD_DO_CANCEL_MAG_CAL}[req.kind]
        res = vehicle_manager.run_command(cmd, name=f"calibrate_{req.kind}")
    _raise_if_failed(res, f"calibration {req.kind}")
    return {"status": "ok", "kind": req.kind,
            "note": "watch STATUSTEXT for progress/completion"}


class FirstFlightRequest(BaseModel):
    cells: int = Field(3, ge=2, le=8, description="battery cell count (LiPo)")
    fence_radius_m: float = Field(1000.0, ge=100, le=5000)
    fence_alt_max_m: float = Field(120.0, ge=30, le=400)
    rtl_alt_m: float = Field(60.0, ge=20, le=200)
    apply: bool = False   # false = preview only


def _first_flight_params(req: FirstFlightRequest, cached: dict) -> dict:
    """Operations-safety bundle. Per-cell voltages: warn the operator (via the
    guardian) BEFORE the autopilot acts — layered thresholds by design.

    Firmware-aware (same story as MAV_GCS_SYSID): newer ArduPlane renamed the
    arming params (ARMING_SKIPCHK/ARMING_REQUIRE, discovered live on 4.8) and
    moved RTL altitude to RTL_ALTITUDE in meters (was ALT_HOLD_RTL in cm).
    `cached` = the synced param table; empty = assume the new names (preview).
    """
    c = req.cells
    params = {
        # GCS + RC loss -> circle briefly, then RTL.
        "FS_GCS_ENABL": 1, "THR_FAILSAFE": 1,
        "FS_SHORT_ACTN": 0, "FS_LONG_ACTN": 1,
        # Battery: low -> RTL, critical -> RTL (operator may retune to Land).
        "BATT_LOW_VOLT": round(3.5 * c, 2), "BATT_FS_LOW_ACT": 1,
        "BATT_CRT_VOLT": round(3.3 * c, 2), "BATT_FS_CRT_ACT": 1,
        # Circular geofence + altitude lid, breach -> RTL.
        "FENCE_TYPE": 3, "FENCE_RADIUS": req.fence_radius_m,
        "FENCE_ALT_MAX": req.fence_alt_max_m, "FENCE_ACTION": 1,
        "FENCE_ENABLE": 1,
        # Waypoint acceptance radius as validated for spray patterns.
        "WP_RADIUS": 30,
    }
    # Arming: every one of ArduPilot's own checks ON for the real airframe.
    if "ARMING_CHECK" in cached:
        params["ARMING_CHECK"] = 1
    else:
        params["ARMING_SKIPCHK"] = 0   # skip nothing
        params["ARMING_REQUIRE"] = 1
    # Come home at a safe height.
    if "ALT_HOLD_RTL" in cached:
        params["ALT_HOLD_RTL"] = int(req.rtl_alt_m * 100)  # cm
    else:
        params["RTL_ALTITUDE"] = req.rtl_alt_m             # m
    return params


def _guardian_for_cells(cells: int) -> dict:
    # Guardian thresholds sit ABOVE the vehicle's (3.5/3.3 per cell): the GCS
    # warns and acts first, the autopilot remains the last line.
    return {"batt_warn_volt": round(3.65 * cells, 2),
            "batt_rtl_volt": round(3.55 * cells, 2)}


@router.post("/first-flight-params")
def first_flight_params(req: FirstFlightRequest = FirstFlightRequest()):
    """Preview (apply=false) or atomically apply the first-flight safety
    bundle + matching guardian thresholds."""
    cached = (vehicle_manager.get_cached_params()
              if vehicle_manager.connected else {})
    params = _first_flight_params(req, cached)
    guardian_cfg = _guardian_for_cells(req.cells)
    if not req.apply:
        return {"status": "preview", "params": params,
                "guardian": guardian_cfg,
                "note": "POST with apply=true to write (all-or-nothing)"}
    _require_bench()
    res = vehicle_manager.set_params_atomic(params)
    if not res.get("ok"):
        if res.get("rejected"):
            raise HTTPException(422, {"message": "bundle rejected by validation",
                                      "param": res.get("rejected"),
                                      "error": res.get("error")})
        raise HTTPException(502, {"message": "vehicle did not confirm the bundle; "
                                             "batch rolled back",
                                  "failed": res.get("failed"),
                                  "rolled_back": res.get("rolled_back", [])})
    applied_guardian = vehicle_manager.set_guardian_config(guardian_cfg)
    log_event("bench", "first_flight_bundle_applied", cells=req.cells,
              params=params)
    return {"status": "ok", "verified": True, "params": params,
            "guardian": applied_guardian}

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app import onboard_fence, preflight
from app.guardian import config_to_dict
from app.vehicle_manager import vehicle_manager

router = APIRouter()

# --- Geofence (ArduPlane FENCE_* params) ---
# FENCE_TYPE is a bitmask: 1 = max altitude, 2 = circle, 4 = polygon, 8 = min alt.
# We drive a circle + max-altitude fence (3).
FENCE_TYPE_CIRCLE_ALT = 3


class GeofenceConfig(BaseModel):
    enable: bool
    radius: float = Field(300.0, gt=0, le=50000)    # m
    alt_max: float = Field(120.0, gt=0, le=10000)   # m
    action: int = Field(1, ge=0, le=6)              # 0 = report only, 1 = RTL/land


class FailsafeConfig(BaseModel):
    batt_low_volt: float = 10.5
    batt_low_action: int = 2    # 0 none, 1 RTL, 2 Land (ArduPlane BATT_FS_LOW_ACT)
    batt_crit_volt: float = 10.0
    batt_crit_action: int = 1
    gcs_enable: int = 1         # FS_GCS_ENABL: 0 disabled, 1 enabled
    rc_enable: bool = True      # THR_FAILSAFE
    rc_long_action: int = 1     # FS_LONG_ACTN: 0 continue, 1 RTL


# NOTE: plain `def` handlers run in FastAPI's threadpool so the multi-second
# param reads here never freeze the event loop (RTL/disarm/telemetry stay live).
def _require_connected():
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")


def _raise_if_not_applied(res: dict, what: str):
    """M3: the fence/failsafe is applied atomically (set_params_atomic). Anything
    but a clean all-verified result fails loudly — a validation reject is a 422,
    a mid-batch write failure (which rolled the batch back) is a 502 — so the
    operator never believes a fence/failsafe took when it didn't, and never ends
    up with a half-applied one."""
    if res.get("ok"):
        return
    if res.get("rejected"):
        raise HTTPException(422, {
            "message": f"{what} rejected by validation",
            "param": res.get("rejected"), "error": res.get("error"),
        })
    raise HTTPException(502, {
        "message": f"Vehicle did not confirm {what} ({res.get('failed')}); "
                   f"batch rolled back",
        "failed": res.get("failed"),
        "error": res.get("error"),
        "rolled_back": res.get("rolled_back", []),
        "rollback_ok": res.get("rollback_ok"),
    })


@router.get("/geofence")
def get_geofence():
    _require_connected()
    p = vehicle_manager.get_params(
        ["FENCE_ENABLE", "FENCE_RADIUS", "FENCE_ALT_MAX", "FENCE_ACTION", "FENCE_TYPE"])
    return {
        "enable": bool(p.get("FENCE_ENABLE", 0)),
        "radius": p.get("FENCE_RADIUS", 300.0),
        "alt_max": p.get("FENCE_ALT_MAX", 120.0),
        "action": int(p.get("FENCE_ACTION", 1)),
        "type": int(p.get("FENCE_TYPE", FENCE_TYPE_CIRCLE_ALT)),
        "raw": p,
    }


@router.post("/geofence")
def set_geofence(cfg: GeofenceConfig):
    _require_connected()
    res = vehicle_manager.set_params_atomic({
        "FENCE_TYPE": FENCE_TYPE_CIRCLE_ALT,
        "FENCE_RADIUS": cfg.radius,
        "FENCE_ALT_MAX": cfg.alt_max,
        "FENCE_ACTION": cfg.action,
        "FENCE_ENABLE": 1 if cfg.enable else 0,
    })
    _raise_if_not_applied(res, "geofence")
    return {"status": "ok", "verified": True, **cfg.model_dump()}


@router.get("/failsafe")
def get_failsafe():
    _require_connected()
    p = vehicle_manager.get_params([
        "BATT_LOW_VOLT", "BATT_FS_LOW_ACT", "BATT_CRT_VOLT", "BATT_FS_CRT_ACT",
        "FS_GCS_ENABL", "THR_FAILSAFE", "FS_LONG_ACTN"])
    return {
        "batt_low_volt": p.get("BATT_LOW_VOLT", 10.5),
        "batt_low_action": int(p.get("BATT_FS_LOW_ACT", 2)),
        "batt_crit_volt": p.get("BATT_CRT_VOLT", 10.0),
        "batt_crit_action": int(p.get("BATT_FS_CRT_ACT", 1)),
        "gcs_enable": int(p.get("FS_GCS_ENABL", 1)),
        "rc_enable": bool(p.get("THR_FAILSAFE", 1)),
        "rc_long_action": int(p.get("FS_LONG_ACTN", 1)),
        "raw": p,
    }


@router.get("/preflight")
def get_preflight():
    """Server-side go/no-go verdict (M6). The same evaluation gates the arm/
    takeoff endpoints, so what the UI shows is what the backend enforces."""
    return preflight.evaluate(vehicle_manager.snapshot(),
                              vehicle_manager.cached_value("FENCE_ENABLE"))


# --- Guardian: GCS-side failsafe monitors + emergency state machine ---
# All fields optional: a POST updates only what it names (partial config).

class GuardianConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    gps_min_sats: Optional[int] = Field(None, ge=0, le=30)
    gps_action: Optional[Literal["warn", "rtl"]] = None
    batt_warn_volt: Optional[float] = Field(None, ge=0, le=100)
    batt_rtl_volt: Optional[float] = Field(None, ge=0, le=100)
    batt_action: Optional[Literal["warn", "rtl"]] = None
    batt_low_s: Optional[float] = Field(None, ge=0, le=120)
    link_warn_level: Optional[Literal["degraded", "poor", "critical"]] = None
    pack_capacity_mah: Optional[float] = Field(None, ge=0, le=200000)
    margin_reserve_s: Optional[float] = Field(None, ge=0, le=3600)
    margin_action: Optional[Literal["warn", "rtl"]] = None
    margin_low_s: Optional[float] = Field(None, ge=0, le=120)
    margin_min_speed: Optional[float] = Field(None, ge=1, le=100)
    ekf_action: Optional[Literal["warn", "rtl"]] = None
    ekf_var_warn: Optional[float] = Field(None, ge=0, le=5)
    vibe_warn_ms2: Optional[float] = Field(None, ge=0, le=200)
    vibe_action: Optional[Literal["warn", "rtl"]] = None
    vibe_sustained_s: Optional[float] = Field(None, ge=0, le=120)
    vibe_clip_warn: Optional[int] = Field(None, ge=0, le=1000)
    airspeed_warn_ms: Optional[float] = Field(None, ge=0, le=100)
    airspeed_min_ms: Optional[float] = Field(None, ge=0, le=100)
    airspeed_action: Optional[Literal["warn", "rtl"]] = None
    airspeed_low_s: Optional[float] = Field(None, ge=0, le=60)
    airborne_alt_m: Optional[float] = Field(None, ge=0, le=100)
    # Bank angle. Capped at 90: past vertical the number stops meaning
    # "a steep turn" and the monitor would never fire again.
    bank_warn_deg: Optional[float] = Field(None, ge=5, le=90)
    bank_action: Optional[Literal["warn", "rtl"]] = None
    bank_sustained_s: Optional[float] = Field(None, ge=0, le=60)
    bank_low_alt_m: Optional[float] = Field(None, ge=0, le=200)
    bank_low_alt_factor: Optional[float] = Field(None, gt=0, le=1)
    keepout_action: Optional[Literal["warn", "rtl"]] = None
    keepout_sustained_s: Optional[float] = Field(None, ge=0, le=60)


class KeepoutLoad(BaseModel):
    """Rings the current mission was planned against, for the live proximity
    monitor. Accepts the coverage response's `zones` shape directly, so the UI
    can forward what it already has instead of reshaping it."""
    # dict (kind -> [zone]) or a flat list of zones; validated in keepout_watch.
    zones: object
    hazard_buffer_m: float = Field(20.0, ge=0, le=500)
    # Also push the hazard rings to the flight controller as polygon exclusion
    # fences, so they survive link loss. Defaults ON: the whole reason the
    # monitor exists is that a hazard is worth avoiding, and a hazard is no
    # less dangerous when the laptop is out of range. Opt out for a bench run
    # where you don't want the FC's fence storage touched.
    push_to_vehicle: bool = True


@router.get("/keepouts")
def get_keepouts():
    """What the live proximity monitor is armed with. `known: false` means it
    cannot judge — which the UI must show as unknown, never as clear."""
    return vehicle_manager.keepout_status()


@router.post("/keepouts")
def load_keepouts(req: KeepoutLoad):
    """Arm the monitor with this mission's zones.

    Deliberately NOT part of mission upload: the aircraft can fly a mission
    the GCS didn't plan, and pretending we know the zones in that case would
    be worse than admitting we don't. Mission upload CLEARS these instead.
    """
    try:
        prepared = vehicle_manager.set_mission_keepouts(
            req.zones, req.hazard_buffer_m)
    except ValueError as exc:
        raise HTTPException(422, str(exc))

    # Same call arms the SOFT monitor and the HARD onboard fence, from the same
    # prepared rings. Deliberately one endpoint: the 86c6a6e bug was a fully
    # built monitor that nothing ever armed, because arming lived in somebody
    # else's layer. A second endpoint here would recreate that seam exactly.
    fence = {"attempted": False}
    if req.push_to_vehicle and vehicle_manager.connected:
        fence["attempted"] = True
        try:
            built = onboard_fence.build_exclusion_items(
                prepared, home=vehicle_manager.home_position())
            fence.update(vehicle_manager.upload_fence(built["items"]))
            fence["polygons"] = built["polygons"]
            fence["not_fenced"] = built["skipped"]
        except ValueError as exc:
            # Geometry we refuse to fence (home inside an exclusion, a
            # pathological ring, over budget). The monitor is still armed, so
            # report loudly rather than failing the whole request -- but never
            # let this read as a successful fence.
            fence.update({"ok": False, "error": str(exc)})

    return {"status": "ok", "hazards": prepared["n_hazards"],
            "keepouts": prepared["n_keepouts"],
            "dropped": prepared["dropped"],
            "hazard_buffer_m": prepared["hazard_buffer_m"],
            "fence": fence}


@router.delete("/keepouts")
def clear_keepouts(clear_fence: bool = False):
    """Disarm the live monitor. Does NOT clear the onboard fence by default.

    The asymmetry is deliberate. Stale RINGS are worse than none -- they read
    as a confident all-clear over ground nobody surveyed, so the monitor
    forgets them (and mission upload clears it for the same reason). A stale
    exclusion FENCE is the opposite: the wire it marks did not move when the
    mission changed, and an exclusion polygon only ever costs something if you
    try to fly into it. Removing it silently takes away airframe protection.

    So clearing the fence is an explicit act -- `?clear_fence=true` -- for when
    the operator genuinely means "this aircraft no longer has surveyed
    hazards", not a side effect of tidying up a monitor.
    """
    vehicle_manager.clear_mission_keepouts(reason="operator")
    out = {"status": "cleared", "fence_cleared": False}
    if clear_fence and vehicle_manager.connected:
        out["fence"] = vehicle_manager.clear_fence()
        out["fence_cleared"] = bool(out["fence"].get("ok"))
    return out


@router.get("/guardian")
def get_guardian():
    """Guardian config + live verdicts. Works without a vehicle (config is
    GCS-side); state is meaningful once connected."""
    return {"config": config_to_dict(vehicle_manager.guardian_config),
            "state": vehicle_manager.guardian_state()}


@router.post("/guardian")
def set_guardian(update: GuardianConfigUpdate):
    updates = {k: v for k, v in update.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(422, "no guardian settings provided")
    # The two-threshold pair must stay ordered whichever half is updated.
    cfg = vehicle_manager.guardian_config
    warn = updates.get("batt_warn_volt", cfg.batt_warn_volt)
    rtl = updates.get("batt_rtl_volt", cfg.batt_rtl_volt)
    if rtl > warn:
        raise HTTPException(422, f"batt_rtl_volt ({rtl}) must not exceed "
                                 f"batt_warn_volt ({warn})")
    as_warn = updates.get("airspeed_warn_ms", cfg.airspeed_warn_ms)
    as_min = updates.get("airspeed_min_ms", cfg.airspeed_min_ms)
    if as_min > as_warn:
        raise HTTPException(422, f"airspeed_min_ms ({as_min}) must not exceed "
                                 f"airspeed_warn_ms ({as_warn})")
    return {"status": "ok",
            "config": vehicle_manager.set_guardian_config(updates)}


@router.post("/failsafe")
def set_failsafe(cfg: FailsafeConfig):
    _require_connected()
    res = vehicle_manager.set_params_atomic({
        "BATT_LOW_VOLT": cfg.batt_low_volt,
        "BATT_FS_LOW_ACT": cfg.batt_low_action,
        "BATT_CRT_VOLT": cfg.batt_crit_volt,
        "BATT_FS_CRT_ACT": cfg.batt_crit_action,
        "FS_GCS_ENABL": cfg.gcs_enable,
        "THR_FAILSAFE": 1 if cfg.rc_enable else 0,
        "FS_LONG_ACTN": cfg.rc_long_action,
    })
    _raise_if_not_applied(res, "failsafe")
    return {"status": "ok", "verified": True, **cfg.model_dump()}

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
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

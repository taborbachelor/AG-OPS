from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from app.vehicle_manager import vehicle_manager

router = APIRouter()

# --- Geofence (ArduPlane FENCE_* params) ---
# FENCE_TYPE is a bitmask: 1 = max altitude, 2 = circle, 4 = polygon, 8 = min alt.
# We drive a circle + max-altitude fence (3).
FENCE_TYPE_CIRCLE_ALT = 3


class GeofenceConfig(BaseModel):
    enable: bool
    radius: float = 300.0       # m
    alt_max: float = 120.0      # m
    action: int = 1             # 0 = report only, 1 = RTL/land


class FailsafeConfig(BaseModel):
    batt_low_volt: float = 10.5
    batt_low_action: int = 2    # 0 none, 1 RTL, 2 Land (ArduPlane BATT_FS_LOW_ACT)
    batt_crit_volt: float = 10.0
    batt_crit_action: int = 1
    gcs_enable: int = 1         # FS_GCS_ENABL: 0 disabled, 1 enabled
    rc_enable: bool = True      # THR_FAILSAFE
    rc_long_action: int = 1     # FS_LONG_ACTN: 0 continue, 1 RTL


def _require_connected():
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")


@router.get("/geofence")
async def get_geofence():
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
async def set_geofence(cfg: GeofenceConfig):
    _require_connected()
    vehicle_manager.set_params({
        "FENCE_TYPE": FENCE_TYPE_CIRCLE_ALT,
        "FENCE_RADIUS": cfg.radius,
        "FENCE_ALT_MAX": cfg.alt_max,
        "FENCE_ACTION": cfg.action,
        "FENCE_ENABLE": 1 if cfg.enable else 0,
    })
    return {"status": "ok", **cfg.model_dump()}


@router.get("/failsafe")
async def get_failsafe():
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


@router.post("/failsafe")
async def set_failsafe(cfg: FailsafeConfig):
    _require_connected()
    vehicle_manager.set_params({
        "BATT_LOW_VOLT": cfg.batt_low_volt,
        "BATT_FS_LOW_ACT": cfg.batt_low_action,
        "BATT_CRT_VOLT": cfg.batt_crit_volt,
        "BATT_FS_CRT_ACT": cfg.batt_crit_action,
        "FS_GCS_ENABL": cfg.gcs_enable,
        "THR_FAILSAFE": 1 if cfg.rc_enable else 0,
        "FS_LONG_ACTN": cfg.rc_long_action,
    })
    return {"status": "ok", **cfg.model_dump()}

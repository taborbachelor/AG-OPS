from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.vehicle_manager import vehicle_manager

router = APIRouter()


class ModeRequest(BaseModel):
    mode: str


@router.get("/modes")
async def get_modes():
    return {"modes": vehicle_manager.get_available_modes()}


@router.post("/mode")
async def set_mode(req: ModeRequest):
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    vehicle_manager.set_mode(req.mode)
    return {"status": "ok", "mode": req.mode}


class ArmRequest(BaseModel):
    force: bool = False


class TakeoffRequest(BaseModel):
    alt: float = 100.0
    force: bool = False


@router.post("/arm")
async def arm(req: ArmRequest = ArmRequest()):
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    result = vehicle_manager.arm(force=req.force)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "arming failed"))
    return {"status": "armed", **result}


@router.post("/disarm")
async def disarm(req: ArmRequest = ArmRequest()):
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    vehicle_manager.disarm(force=req.force)
    return {"status": "disarmed"}


@router.post("/takeoff")
async def takeoff(req: TakeoffRequest = TakeoffRequest()):
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    result = vehicle_manager.takeoff(alt=req.alt, force=req.force)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "takeoff failed"))
    return {"status": "takeoff", **result}


class ParamUpdate(BaseModel):
    name: str
    value: float


@router.post("/params")
async def set_param(req: ParamUpdate):
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    vehicle_manager.set_param(req.name, req.value)
    return {"status": "ok", "param": req.name, "value": req.value}

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


@router.post("/arm")
async def arm():
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    vehicle_manager.arm()
    return {"status": "armed"}


@router.post("/disarm")
async def disarm():
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    vehicle_manager.disarm()
    return {"status": "disarmed"}


class ParamUpdate(BaseModel):
    name: str
    value: float


@router.post("/params")
async def set_param(req: ParamUpdate):
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    vehicle_manager.set_param(req.name, req.value)
    return {"status": "ok", "param": req.name, "value": req.value}

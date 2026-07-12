from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from app.vehicle_manager import vehicle_manager

router = APIRouter()


@router.websocket("/rc")
async def rc_override_ws(ws: WebSocket):
    """Receive a stream of {channels:[...]} from a laptop-connected transmitter/
    gamepad and forward each as an RC override. Releases the override on
    disconnect so the plane never keeps stuck stick positions."""
    await ws.accept()
    try:
        while True:
            data = await ws.receive_json()
            if vehicle_manager.connected:
                vehicle_manager.send_rc_override(data.get("channels", []))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        vehicle_manager.release_rc_override()


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
    alt: float = Field(100.0, gt=0, le=2000)
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


@router.post("/land")
async def land():
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    result = vehicle_manager.land()
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "landing failed"))
    return {"status": "landing", **result}


@router.get("/params")
async def get_all_params():
    """Full parameter table — takes a few seconds; telemetry pauses while the
    link is dedicated to the transfer (ground/config activity)."""
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    params = vehicle_manager.get_all_params()
    if not params:
        raise HTTPException(504, "Parameter download timed out")
    return {"params": params, "count": len(params)}


class ParamUpdate(BaseModel):
    name: str
    value: float


@router.post("/params")
async def set_param(req: ParamUpdate):
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    vehicle_manager.set_param(req.name, req.value)
    return {"status": "ok", "param": req.name, "value": req.value}

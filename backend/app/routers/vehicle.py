from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from app.vehicle_manager import vehicle_manager

router = APIRouter()

# NOTE: handlers that talk to the vehicle are deliberately plain `def`, not
# `async def`: FastAPI runs them in its threadpool, so a slow/blocking
# pymavlink call (serial dial, 30s heartbeat wait, param download, ack waits)
# can never freeze the event loop — emergency commands (RTL, disarm) and the
# telemetry WebSocket must stay responsive at all times.


@router.websocket("/rc")
async def rc_override_ws(ws: WebSocket):
    """Receive a stream of {channels:[...]} from a laptop-connected transmitter/
    gamepad and forward each as an RC override. Releases the override on
    disconnect so the plane never keeps stuck stick positions."""
    await ws.accept()
    try:
        while True:
            data = await ws.receive_json()
            # One malformed frame (or the link dropping mid-send) must NEVER
            # kill this handler: the pilot's stick input would silently stop
            # working while the socket still looks open. Skip bad frames and
            # keep reading.
            try:
                channels = data.get("channels") if isinstance(data, dict) else None
                if isinstance(channels, (list, tuple)) and vehicle_manager.connected:
                    vehicle_manager.send_rc_override(list(channels))
            except Exception:
                continue
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
def set_mode(req: ModeRequest):
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    # set_mode() is ack-checked: False means the vehicle doesn't know the mode
    # or refused the change. Never tell the operator "ok" for a mode change
    # that didn't happen (e.g. they think the plane is landing and it isn't).
    if not vehicle_manager.set_mode(req.mode):
        raise HTTPException(400, f"Vehicle rejected mode change to {req.mode}")
    return {"status": "ok", "mode": req.mode}


class ArmRequest(BaseModel):
    force: bool = False


class TakeoffRequest(BaseModel):
    alt: float = Field(100.0, gt=0, le=2000)
    force: bool = False


@router.post("/arm")
def arm(req: ArmRequest = ArmRequest()):
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    result = vehicle_manager.arm(force=req.force)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "arming failed"))
    return {"status": "armed", **result}


@router.post("/disarm")
def disarm(req: ArmRequest = ArmRequest()):
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    result = vehicle_manager.disarm(force=req.force)
    if not result.get("ok"):
        # Never report "disarmed" when the vehicle rejected or didn't ack the
        # disarm — the prop may still be spinning.
        raise HTTPException(400, result.get("error") or "disarm failed")
    return {"status": "disarmed", **result}


@router.post("/takeoff")
def takeoff(req: TakeoffRequest = TakeoffRequest()):
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    result = vehicle_manager.takeoff(alt=req.alt, force=req.force)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "takeoff failed"))
    return {"status": "takeoff", **result}


@router.post("/land")
def land():
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    result = vehicle_manager.land()
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "landing failed"))
    return {"status": "landing", **result}


@router.get("/params")
def get_all_params():
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
def set_param(req: ParamUpdate):
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    result = vehicle_manager.set_param(req.name, req.value)
    # Echo-verified (M1b): surface what the FC actually stored, and fail loudly
    # if it didn't take — never report a blind success.
    if not result["verified"]:
        raise HTTPException(502, {
            "message": f"Vehicle did not confirm {req.name}",
            "param": req.name,
            "requested": result["requested"],
            "accepted": result["accepted"],
            "error": result.get("error"),
        })
    return {
        "status": "ok",
        "param": req.name,
        "requested": result["requested"],
        "value": result["accepted"],
        "verified": True,
    }

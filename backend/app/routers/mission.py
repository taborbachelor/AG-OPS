from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Literal
from app.vehicle_manager import vehicle_manager

router = APIRouter()

CommandType = Literal["TAKEOFF", "WAYPOINT", "LOITER", "LAND", "RTL"]


class MissionItem(BaseModel):
    command: CommandType = "WAYPOINT"
    lat: float
    lon: float
    alt: float
    param1: float = 0.0
    # Loiter radius in meters (param3 for LOITER items; ignored otherwise).
    radius: float = 0.0


class MissionUpload(BaseModel):
    items: list[MissionItem]


# NOTE: plain `def` handlers run in FastAPI's threadpool so long mission
# transfers never freeze the event loop (RTL/disarm/telemetry stay live).


@router.get("/download")
def download_mission():
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    items = vehicle_manager.download_mission()
    return {"items": items}


@router.post("/upload")
def upload_mission(mission: MissionUpload):
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    items = [i.model_dump() for i in mission.items]
    result = vehicle_manager.upload_mission(items)
    if not result.get("ok"):
        raise HTTPException(500, f"Mission upload failed: {result}")
    return result


@router.post("/start")
def start_mission():
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    # Ack-checked: never report "mission started" if the vehicle refused AUTO.
    if not vehicle_manager.set_mode("AUTO"):
        raise HTTPException(400, "Vehicle rejected switch to AUTO — mission NOT started")
    return {"status": "mission started"}

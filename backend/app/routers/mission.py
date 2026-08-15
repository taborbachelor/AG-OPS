from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Literal
from app.vehicle_manager import vehicle_manager

router = APIRouter()

CommandType = Literal["TAKEOFF", "WAYPOINT", "LOITER", "LAND", "RTL"]


class MissionItem(BaseModel):
    """Bounds matter beyond sanity: mission_item_int packs lat/lon as
    int32 * 1e7 — an out-of-range value would raise a struct error mid-
    transfer, aborting the upload with the vehicle left mid-transaction."""
    command: CommandType = "WAYPOINT"
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    alt: float = Field(ge=-500, le=10000)          # m relative; generous sanity
    param1: float = Field(0.0, ge=-100000, le=100000)
    # Loiter radius in meters (param3 for LOITER items; negative = CCW).
    radius: float = Field(0.0, ge=-100000, le=100000)


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

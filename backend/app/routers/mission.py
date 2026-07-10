from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.vehicle_manager import vehicle_manager

router = APIRouter()


class Waypoint(BaseModel):
    lat: float
    lon: float
    alt: float


class MissionUpload(BaseModel):
    waypoints: list[Waypoint]


@router.get("/download")
async def download_mission():
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    waypoints = vehicle_manager.download_mission()
    return {"waypoints": waypoints}


@router.post("/upload")
async def upload_mission(mission: MissionUpload):
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    wps = [{"lat": wp.lat, "lon": wp.lon, "alt": wp.alt} for wp in mission.waypoints]
    success = vehicle_manager.upload_mission(wps)
    if not success:
        raise HTTPException(500, "Mission upload failed")
    return {"status": "uploaded", "count": len(wps)}


@router.post("/start")
async def start_mission():
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    vehicle_manager.set_mode("AUTO")
    return {"status": "mission started"}

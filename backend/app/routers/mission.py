from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from dronekit import LocationGlobalRelative, Command
from pymavlink import mavutil
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
    v = vehicle_manager.vehicle
    if not v:
        raise HTTPException(400, "Not connected")
    cmds = v.commands
    cmds.download()
    cmds.wait_ready()
    waypoints = []
    for cmd in cmds:
        if cmd.command == mavutil.mavlink.MAV_CMD_NAV_WAYPOINT:
            waypoints.append({"lat": cmd.x, "lon": cmd.y, "alt": cmd.z})
    return {"waypoints": waypoints}


@router.post("/upload")
async def upload_mission(mission: MissionUpload):
    v = vehicle_manager.vehicle
    if not v:
        raise HTTPException(400, "Not connected")
    cmds = v.commands
    cmds.clear()
    for wp in mission.waypoints:
        cmd = Command(
            0, 0, 0,
            mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT,
            mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
            0, 0, 0, 0, 0, 0,
            wp.lat, wp.lon, wp.alt,
        )
        cmds.add(cmd)
    cmds.upload()
    return {"status": "uploaded", "count": len(mission.waypoints)}


@router.post("/start")
async def start_mission():
    v = vehicle_manager.vehicle
    if not v:
        raise HTTPException(400, "Not connected")
    v.mode = "AUTO"
    return {"status": "mission started"}

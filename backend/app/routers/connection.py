from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import serial.tools.list_ports
from app.vehicle_manager import vehicle_manager

router = APIRouter()


class ConnectRequest(BaseModel):
    connection_string: str
    baud: int = 57600


@router.get("/ports")
async def list_serial_ports():
    ports = serial.tools.list_ports.comports()
    return [{"device": p.device, "description": p.description} for p in ports]


@router.post("/connect")
async def connect_vehicle(req: ConnectRequest):
    if vehicle_manager.connected:
        raise HTTPException(400, "Already connected. Disconnect first.")
    try:
        vehicle_manager.connect(req.connection_string, req.baud)
        return {"status": "connected", "connection": req.connection_string}
    except ConnectionError as e:
        raise HTTPException(500, str(e))


@router.post("/disconnect")
async def disconnect_vehicle():
    vehicle_manager.disconnect()
    return {"status": "disconnected"}


@router.get("/status")
async def connection_status():
    return {
        "connected": vehicle_manager.connected,
        "connection_string": vehicle_manager.connection_string,
        "reconnecting": vehicle_manager.reconnecting,
    }

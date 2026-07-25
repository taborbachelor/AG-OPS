from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import serial.tools.list_ports
from app.vehicle_manager import vehicle_manager

router = APIRouter()


class ConnectRequest(BaseModel):
    connection_string: str
    baud: int = 57600


# NOTE: blocking handlers are plain `def` (FastAPI runs them in a threadpool)
# so a slow serial dial or 30s heartbeat wait can never freeze the event loop
# and with it the whole GCS API (RTL/disarm/telemetry).


@router.get("/ports")
def list_serial_ports():
    ports = serial.tools.list_ports.comports()
    return [{"device": p.device, "description": p.description} for p in ports]


@router.post("/connect")
def connect_vehicle(req: ConnectRequest):
    if vehicle_manager.connected:
        raise HTTPException(400, "Already connected. Disconnect first.")
    if vehicle_manager.reconnecting:
        # A manual connect racing the auto-reconnect thread's own connect()
        # would clobber the connection and spawn duplicate telemetry loops.
        raise HTTPException(409, "Auto-reconnect in progress. Disconnect first to cancel it.")
    try:
        vehicle_manager.connect(req.connection_string, req.baud)
        return {"status": "connected", "connection": req.connection_string}
    except ConnectionError as e:
        raise HTTPException(500, str(e))


@router.post("/disconnect")
def disconnect_vehicle():
    vehicle_manager.disconnect()
    return {"status": "disconnected"}


@router.get("/status")
async def connection_status():
    return {
        "connected": vehicle_manager.connected,
        "connection_string": vehicle_manager.connection_string,
        "reconnecting": vehicle_manager.reconnecting,
        # M2: connection state machine + link identity.
        "link_state": vehicle_manager._link_state,
        "gcs_sysid": vehicle_manager._sysid,
        "vehicle_sysid": vehicle_manager._vehicle_sysid,
        "capabilities": vehicle_manager._capabilities,
    }

import asyncio
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.vehicle_manager import vehicle_manager

router = APIRouter()


@router.get("/")
async def get_telemetry():
    # snapshot() is the single, thread-safe source of vehicle state (includes
    # connected/link health/statustext) — never read .telemetry directly.
    return vehicle_manager.snapshot()


@router.websocket("/ws")
async def telemetry_ws(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            await websocket.send_text(json.dumps(vehicle_manager.snapshot()))
            await asyncio.sleep(0.1)  # 10Hz
    except WebSocketDisconnect:
        pass

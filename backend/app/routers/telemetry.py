import asyncio
import json
from dataclasses import asdict
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.vehicle_manager import vehicle_manager

router = APIRouter()


@router.get("/")
async def get_telemetry():
    return asdict(vehicle_manager.telemetry)


@router.websocket("/ws")
async def telemetry_ws(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = asdict(vehicle_manager.telemetry)
            data["connected"] = vehicle_manager.connected
            await websocket.send_text(json.dumps(data))
            await asyncio.sleep(0.1)  # 10Hz
    except WebSocketDisconnect:
        pass

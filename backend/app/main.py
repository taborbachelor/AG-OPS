from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import telemetry, mission, connection, vehicle

app = FastAPI(title="RC Plane GCS", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(connection.router, prefix="/api/connection", tags=["connection"])
app.include_router(telemetry.router, prefix="/api/telemetry", tags=["telemetry"])
app.include_router(mission.router, prefix="/api/mission", tags=["mission"])
app.include_router(vehicle.router, prefix="/api/vehicle", tags=["vehicle"])


@app.get("/api/health")
async def health():
    return {"status": "ok"}

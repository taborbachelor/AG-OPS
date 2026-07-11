import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from app.routers import telemetry, mission, connection, vehicle, safety, logs

logger = logging.getLogger("gcs")

app = FastAPI(title="RC Plane GCS", version="0.1.0")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Never let an unexpected error take the server down or leak a stack trace
    to the UI — return a clean 500 the frontend can surface."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"Server error: {exc}"})

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
app.include_router(safety.router, prefix="/api/safety", tags=["safety"])
app.include_router(logs.router, prefix="/api/logs", tags=["logs"])


@app.get("/api/health")
async def health():
    return {"status": "ok"}

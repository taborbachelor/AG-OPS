import logging
import math
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.eventlog import log_event
from app.routers import telemetry, mission, connection, vehicle, safety, logs, coverage, coverage_multi, zones, orders, fields, sim, bench

# Make app loggers (incl. gcs.events mirrors) visible in the server console;
# no-ops if the root logger is already configured by the host process.
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)s %(message)s")

logger = logging.getLogger("gcs")

app = FastAPI(title="RC Plane GCS", version="0.1.0")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Never let an unexpected error take the server down or leak a stack trace
    to the UI — return a clean 500 the frontend can surface."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    log_event("core", "unhandled_exception", level="ERROR",
              method=request.method, path=request.url.path, error=str(exc))
    return JSONResponse(status_code=500, content={"detail": f"Server error: {exc}"})


def _serializable_422(obj):
    """Make pydantic error details strictly JSON-serializable.

    A 422 echoes the offending input back to the client; when that input is
    NaN/Infinity (json.loads accepts the literals) the strict response
    serializer raises and the client sees a 500 instead of the validation
    error. Scrub non-finite floats and stringify anything exotic."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else repr(obj)
    if isinstance(obj, dict):
        return {k: _serializable_422(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serializable_422(v) for v in obj]
    if obj is None or isinstance(obj, (str, int, bool)):
        return obj
    return repr(obj)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422,
                        content={"detail": _serializable_422(exc.errors())})

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
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
app.include_router(coverage.router, prefix="/api/coverage", tags=["coverage"])
app.include_router(coverage_multi.router, prefix="/api/coverage", tags=["coverage"])
app.include_router(zones.router, prefix="/api/zones", tags=["zones"])
app.include_router(orders.router, prefix="/api/orders", tags=["orders"])
app.include_router(fields.router, prefix="/api/fields", tags=["fields"])
app.include_router(sim.router, prefix="/api/sim", tags=["sim"])
app.include_router(bench.router, prefix="/api/bench", tags=["bench"])


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# Serve the built GCS frontend (single-exe / production mode). API routes are
# registered above, so they win; everything else falls through to the SPA.
if getattr(sys, "frozen", False):
    _static_dir = Path(sys._MEIPASS) / "frontend_build"  # PyInstaller bundle
else:
    _static_dir = Path(__file__).resolve().parents[2] / "frontend" / "build"

if _static_dir.is_dir():
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="gcs")

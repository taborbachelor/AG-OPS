"""Coverage-planning endpoints.

Thin HTTP shell around app.coverage.plan_coverage — all geometry lives in the
pure module so it stays unit-testable without FastAPI. Handlers are plain sync
functions (no awaits needed) so tests can call them directly.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.coverage import plan_coverage

router = APIRouter()


class LatLon(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)


class CoverageRequest(BaseModel):
    # 500 vertices is far beyond any hand-drawn field boundary; the cap guards
    # against pathological payloads rather than real use.
    polygon: list[LatLon] = Field(..., min_length=3, max_length=500)
    swath: float = Field(20.0, gt=0.5, lt=200)   # m between passes
    alt: float = Field(100.0, gt=0, le=500)      # m AGL for every waypoint
    angle: Optional[float] = None                # deg CCW from east; None = auto
    speed: float = 18.0                          # m/s, for the time estimate only


@router.post("/plan")
def plan(req: CoverageRequest):
    """Compute a serpentine spray plan for the given field polygon."""
    try:
        return plan_coverage(
            [p.model_dump() for p in req.polygon],
            swath_m=req.swath,
            alt_m=req.alt,
            angle_deg=req.angle,
            speed_ms=req.speed,
        )
    except ValueError as exc:
        # Geometry-level rejections (degenerate polygon, bad speed, ...)
        # are client errors, not server faults.
        raise HTTPException(400, str(exc))

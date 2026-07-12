"""Coverage-planning endpoints.

Thin HTTP shell around app.coverage.plan_coverage — all geometry lives in the
pure module so it stays unit-testable without FastAPI. Handlers are plain sync
functions (no awaits needed) so tests can call them directly.
"""

import math
from typing import Annotated, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.coverage import EARTH_RADIUS_M, plan_coverage
from app.gis_zones import fetch_zones

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
    # No-spray keepouts: optional so pre-keepout clients are untouched (their
    # responses stay byte-for-byte identical). Vertex-count and shape checks
    # live in plan_coverage (single source of truth) and surface as 400s,
    # matching the other geometry errors. The max_length caps here mirror the
    # polygon guard above: clipping cost is linear in passes x total keepout
    # edges, so unbounded lists would let one unauthenticated request burn
    # minutes of CPU. 100 rings x 500 vertices is far beyond real use.
    keepouts: Optional[list[Annotated[list[LatLon],
                                      Field(max_length=500)]]] = Field(
        None, max_length=100)
    keepout_buffer: float = Field(0.0, ge=0)     # m standoff around keepouts


class AutoCoverageRequest(BaseModel):
    """Plan request that discovers its own keepouts from OSM zones."""
    polygon: list[LatLon] = Field(..., min_length=3, max_length=500)
    swath: float = Field(20.0, gt=0.5, lt=200)
    alt: float = Field(100.0, gt=0, le=500)
    angle: Optional[float] = None
    speed: float = 18.0
    # Per-kind standoffs (m). Water defaults widest: drift into a pond is the
    # costliest mistake (chemical runoff), trees/buildings mostly block spray.
    water_buffer: float = Field(15.0, ge=0)
    tree_buffer: float = Field(10.0, ge=0)
    building_buffer: float = Field(10.0, ge=0)


@router.post("/plan")
def plan(req: CoverageRequest):
    """Compute a serpentine spray plan for the given field polygon.

    Optional keepouts (with keepout_buffer standoff) clip the spray passes;
    requests without them behave exactly as before keepouts existed.
    """
    try:
        return plan_coverage(
            [p.model_dump() for p in req.polygon],
            swath_m=req.swath,
            alt_m=req.alt,
            angle_deg=req.angle,
            speed_ms=req.speed,
            keepouts=(None if req.keepouts is None else
                      [[p.model_dump() for p in kp] for kp in req.keepouts]),
            keepout_buffer_m=req.keepout_buffer,
        )
    except ValueError as exc:
        # Geometry-level rejections (degenerate polygon, bad speed, fully
        # blocked field, ...) are client errors, not server faults.
        raise HTTPException(400, str(exc))


@router.post("/plan_auto")
def plan_auto(req: AutoCoverageRequest):
    """Plan coverage with keepouts auto-fetched from OSM around the field.

    Zone search radius = half the field's bounding-box diagonal + 500 m
    margin (spray drift + zones just past the boundary), capped at 5 km to
    respect the shared Overpass service (fetch_zones caps again anyway).

    Per-kind buffers are applied conservatively: every keepout is clipped
    with the LARGEST buffer among the kinds actually present. A single
    scalar keeps the geometry API simple, and over-standoff errs on the
    side of not spraying — never the reverse (a tree buffered like water
    wastes a little chemical; the opposite would contaminate the pond).

    If the zone lookup fails (Overpass down / rate-limited), degrade
    gracefully instead of 500ing: return the UNCLIPPED plan flagged
    zones_unavailable=true so the operator can still fly and judge
    keepouts visually.
    """
    poly = [p.model_dump() for p in req.polygon]
    lats = [p["lat"] for p in poly]
    lons = [p["lon"] for p in poly]
    clat = sum(lats) / len(lats)
    clon = sum(lons) / len(lons)
    # Half-diagonal of the bounding box in meters, via the same
    # equirectangular approximation the planner itself uses.
    m_per_deg = math.pi / 180.0 * EARTH_RADIUS_M
    span_x = (max(lons) - min(lons)) * m_per_deg * math.cos(math.radians(clat))
    span_y = (max(lats) - min(lats)) * m_per_deg
    radius = min(math.hypot(span_x, span_y) / 2.0 + 500.0, 5000.0)

    plan_kwargs = dict(swath_m=req.swath, alt_m=req.alt,
                       angle_deg=req.angle, speed_ms=req.speed)
    try:
        zones = fetch_zones(clat, clon, radius)
    except RuntimeError:
        try:
            unclipped = plan_coverage(poly, **plan_kwargs)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {**unclipped, "zones": {}, "zones_unavailable": True}

    buffers = {"water": req.water_buffer, "trees": req.tree_buffer,
               "buildings": req.building_buffer}
    keepouts: list[list[dict]] = []
    max_buffer = 0.0
    for kind in ("water", "trees", "buildings"):
        for zone in zones.get(kind, []):
            ring = zone["coords"]
            if len(ring) > 1 and ring[0] == ring[-1]:
                ring = ring[:-1]  # planner wants open rings; zones are closed
            if len(ring) >= 3:
                keepouts.append(ring)
                max_buffer = max(max_buffer, buffers[kind])
    try:
        planned = plan_coverage(poly, **plan_kwargs,
                                keepouts=keepouts,
                                keepout_buffer_m=max_buffer)
    except ValueError as exc:
        # Includes "field fully blocked by keepout zones" — a geometry
        # outcome the client must see, not a server fault.
        raise HTTPException(400, str(exc))
    # Echo the zones so a UI can render what was avoided and why.
    return {**planned, "zones": zones, "zones_unavailable": False}

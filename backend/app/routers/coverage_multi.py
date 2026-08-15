import math
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.coverage_multi import plan_multi
from app.gis_zones import fetch_zones

router = APIRouter()


class LatLon(BaseModel):
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)


class MultiRequest(BaseModel):
    fields: list = Field(min_length=1, max_length=25)  # list[list[LatLon]] checked below
    swath: float = Field(20, gt=0.5, lt=200)
    alt: float = Field(100, gt=0, le=500)
    speed: float = Field(18, gt=1, le=80)
    water_buffer: float = Field(15, ge=0, le=200)
    tree_buffer: float = Field(10, ge=0, le=200)
    building_buffer: float = Field(10, ge=0, le=200)
    home: Optional[LatLon] = None
    # Caller-supplied keepouts (e.g. detected in-field holes: farmsteads,
    # ponds). Merged with the OSM zone keepouts; honored even when the zone
    # service is down.
    keepouts: Optional[list] = Field(None, max_length=100)
    # FAIL-CLOSED default: when the zone service is down the request is
    # refused (502) unless the operator explicitly accepts planning without
    # water/tree/building keepouts.
    allow_missing_zones: bool = False


# Zone-derived keepout rings are bounded like caller keepouts — but instead of
# silently truncating (dropping keepouts = spraying them), an over-limit area
# fails loudly so the operator shrinks the job.
_MAX_ZONE_RINGS = 400
_MAX_ZONE_RING_VERTICES = 2000


@router.post("/plan_multi")
def plan_multi_endpoint(req: MultiRequest):
    """Whole-job planning: every field gets a zone-aware coverage plan, fields
    are ordered for minimal transit (with per-field direction reversal), and
    explicit transit legs are returned so the UI can draw the full flight.
    Zone lookup happens ONCE around the whole job; if the map service is down
    the job degrades to unclipped plans with zones_unavailable=true."""
    # Validate field shapes (pydantic nested-list-of-models is awkward across
    # versions, so coerce/check by hand for clear errors).
    fields = []
    for fi, poly in enumerate(req.fields):
        if not isinstance(poly, list) or not (3 <= len(poly) <= 500):
            raise HTTPException(422, f"field {fi}: needs 3..500 vertices")
        ring = []
        for p in poly:
            try:
                lat, lon = float(p["lat"]), float(p["lon"])
            except (TypeError, KeyError, ValueError):
                raise HTTPException(422, f"field {fi}: vertices must be {{lat,lon}}")
            if not (math.isfinite(lat) and math.isfinite(lon)
                    and -90 <= lat <= 90 and -180 <= lon <= 180):
                raise HTTPException(422, f"field {fi}: coordinate out of range")
            ring.append({"lat": lat, "lon": lon})
        fields.append(ring)

    # One zone fetch covering the whole job area.
    pts = [p for f in fields for p in f]
    clat = sum(p["lat"] for p in pts) / len(pts)
    clon = sum(p["lon"] for p in pts) / len(pts)
    kx = 111320.0 * math.cos(math.radians(clat))
    span = max(
        (math.hypot((p["lat"] - clat) * 111320.0, (p["lon"] - clon) * kx) for p in pts),
        default=0.0,
    )
    radius = min(5000.0, span + 500.0)

    # Caller-supplied keepouts (validated like fields, but only 3+ vertices).
    user_keepouts = []
    for ki, ring in enumerate(req.keepouts or []):
        if not isinstance(ring, list) or len(ring) < 3 or len(ring) > 500:
            raise HTTPException(422, f"keepout {ki}: needs 3..500 vertices")
        clean = []
        for p in ring:
            try:
                lat, lon = float(p["lat"]), float(p["lon"])
            except (TypeError, KeyError, ValueError):
                raise HTTPException(422, f"keepout {ki}: vertices must be {{lat,lon}}")
            if not (math.isfinite(lat) and math.isfinite(lon)
                    and -90 <= lat <= 90 and -180 <= lon <= 180):
                raise HTTPException(422, f"keepout {ki}: coordinate out of range")
            clean.append({"lat": lat, "lon": lon})
        user_keepouts.append(clean)

    zones = {}
    zones_unavailable = False
    buf = max(req.water_buffer, req.tree_buffer, req.building_buffer)
    keepouts = list(user_keepouts)
    try:
        z = fetch_zones(clat, clon, radius)
        zones = z
        for kind in ("water", "trees", "buildings"):
            for zone in z.get(kind, []) or []:
                ring = list(zone.get("coords", []))
                if len(ring) >= 2 and ring[0] == ring[-1]:
                    ring = ring[:-1]
                if len(ring) < 3:
                    continue
                if len(ring) > _MAX_ZONE_RING_VERTICES:
                    raise HTTPException(400, {
                        "message": "a no-spray zone in this area is too "
                                   "complex to clip — shrink the job area",
                        "error": "zone_too_complex"})
                keepouts.append(ring)
        if len(keepouts) > _MAX_ZONE_RINGS:
            # Never silently drop keepouts (dropping = spraying them).
            raise HTTPException(400, {
                "message": f"{len(keepouts)} no-spray zones in this area — "
                           "too many to clip; shrink the job area",
                "error": "too_many_zones"})
        # Same conservative choice as plan_auto: clip with the largest of the
        # per-kind buffers (over-standoff never sprays a keepout).
    except ValueError as e:
        raise HTTPException(422, str(e))
    except RuntimeError as e:
        # FAIL-CLOSED: a job with silently-zero keepouts must never be the
        # default outcome of an Overpass outage. The operator may explicitly
        # accept it; detected in-field holes (user keepouts) still apply.
        if not req.allow_missing_zones:
            raise HTTPException(502, {
                "message": "no-spray zone lookup failed — refusing to plan "
                           "without zone data",
                "error": "zones_unavailable",
                "detail": str(e),
                "hint": "retry, or pass allow_missing_zones=true to plan "
                        "anyway WITHOUT water/tree/building keepouts",
            })
        zones_unavailable = True
        if not user_keepouts:
            keepouts = None

    home = {"lat": req.home.lat, "lon": req.home.lon} if req.home else None
    try:
        result = plan_multi(
            fields, req.swath, req.alt,
            keepouts=keepouts, keepout_buffer_m=buf,
            home=home, speed_ms=req.speed,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    result["zones"] = {} if zones_unavailable else zones
    result["zones_unavailable"] = zones_unavailable
    return result

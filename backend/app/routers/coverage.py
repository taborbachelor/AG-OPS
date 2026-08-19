"""Coverage-planning endpoints.

Thin HTTP shell around app.coverage.plan_coverage — all geometry lives in the
pure module so it stays unit-testable without FastAPI. Handlers are plain sync
functions (no awaits needed) so tests can call them directly.
"""

import math
from typing import Annotated, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.coverage import DEFAULT_MAX_BANK_DEG, EARTH_RADIUS_M, plan_coverage
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
    # m/s, for the time estimate only. Deliberately NOT a Field(gt/le)
    # constraint: on this FastAPI/starlette stack a pydantic rejection echoes
    # the offending input inside the 422 body, and strict json.dumps then
    # 500s when that input is NaN/Infinity — the very bug being fixed. Both
    # handlers call _check_speed() instead (always-serializable rejection).
    speed: float = 18.0
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
    # Ceiling on the bank the PLAN is allowed to command in a turn; passes are
    # ordered to give every reversal the lateral room it needs at speed.
    # 0 disables it and restores the plain adjacent-line serpentine, which
    # demands ~73 deg of bank at the default swath and speed. The achieved
    # geometry always comes back in stats.turn_* whether or not the limit was
    # met — see coverage.py's turn-geometry section.
    max_bank: float = Field(DEFAULT_MAX_BANK_DEG, ge=0, lt=90)
    # Widen each pass to cover the full swath-deep band it sprays, closing the
    # sawtooth strip along a slanted or traced boundary. Spray then reaches up
    # to half a swath past the boundary where the edge slants away (bounded --
    # see coverage.py's headlands section). Set false where overspray is not
    # acceptable: an organic neighbour, a road, a waterway.
    headlands: bool = True


class AutoCoverageRequest(BaseModel):
    """Plan request that discovers its own keepouts from OSM zones."""
    polygon: list[LatLon] = Field(..., min_length=3, max_length=500)
    swath: float = Field(20.0, gt=0.5, lt=200)
    alt: float = Field(100.0, gt=0, le=500)
    angle: Optional[float] = None
    speed: float = 18.0     # checked by _check_speed (see CoverageRequest)
    # Per-kind standoffs (m). Water defaults widest: drift into a pond is the
    # costliest mistake (chemical runoff), trees/buildings mostly block spray.
    water_buffer: float = Field(15.0, ge=0)
    tree_buffer: float = Field(10.0, ge=0)
    building_buffer: float = Field(10.0, ge=0)
    # Powerlines are the one keepout kind that protects the AIRFRAME rather
    # than spray quality, so the default is lateral flight clearance (wider)
    # rather than a drift margin. See gis_zones' module docstring.
    powerline_buffer: float = Field(20.0, ge=0)
    # Caller-supplied keepouts (e.g. CDL-detected in-field holes), merged
    # with the OSM zones and honored even when the zone service is down.
    keepouts: Optional[list[Annotated[list[LatLon],
                                      Field(max_length=500)]]] = Field(
        None, max_length=100)
    # FAIL-CLOSED default: a plan with no zone data is only returned when the
    # operator explicitly accepts flying without no-spray keepouts.
    allow_missing_zones: bool = False
    # FAIL-CLOSED for the same reason, one step further: if a connecting leg
    # still crosses a powerline corridor after rerouting, the plan contains a
    # path that flies through the conductor (measured at 1.7 m from a live
    # 115 kV line before this gate existed). Warning about that is not enough
    # — the operator has to accept it deliberately.
    allow_hazard_crossings: bool = False
    # Ceiling on the bank the PLAN is allowed to command in a turn; passes are
    # ordered to give every reversal the lateral room it needs at speed.
    # 0 disables it and restores the plain adjacent-line serpentine, which
    # demands ~73 deg of bank at the default swath and speed. The achieved
    # geometry always comes back in stats.turn_* whether or not the limit was
    # met — see coverage.py's turn-geometry section.
    max_bank: float = Field(DEFAULT_MAX_BANK_DEG, ge=0, lt=90)
    # Widen each pass to cover the full swath-deep band it sprays, closing the
    # sawtooth strip along a slanted or traced boundary. Spray then reaches up
    # to half a swath past the boundary where the edge slants away (bounded --
    # see coverage.py's headlands section). Set false where overspray is not
    # acceptable: an organic neighbour, a road, a waterway.
    headlands: bool = True


# Same bounds as the multi router's speed field, enforced in the handlers:
# a NaN speed passes a bare float and defeats plan_coverage's
# `speed_ms <= 0` guard (NaN <= 0 is False), poisoning est_time_s and
# crashing response serialization — an unauthenticated 500. It can't be a
# pydantic Field(gt/le) either, because this stack echoes the rejected NaN
# into the 422 body, which strict JSON serialization also turns into a 500.
_SPEED_MIN_MS, _SPEED_MAX_MS = 1.0, 80.0

# Keepout kinds that connecting legs must be FLOWN AROUND rather than
# overflown. Water/trees/buildings protect spray quality — crossing one with
# the sprayer off is free. A powerline is an airframe hazard, so it is the
# only kind here today.
_HAZARD_KINDS = ("powerline",)


def _check_speed(speed: float) -> None:
    """Reject non-finite or out-of-range speeds with a serializable 422."""
    if not (math.isfinite(speed)
            and _SPEED_MIN_MS < speed <= _SPEED_MAX_MS):
        raise HTTPException(
            422, "speed must be a finite value in "
                 f"({_SPEED_MIN_MS:g}, {_SPEED_MAX_MS:g}] m/s")


@router.post("/plan")
def plan(req: CoverageRequest):
    """Compute a serpentine spray plan for the given field polygon.

    Optional keepouts (with keepout_buffer standoff) clip the spray passes;
    requests without them behave exactly as before keepouts existed.
    """
    _check_speed(req.speed)
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
            max_bank_deg=req.max_bank,
            headlands=req.headlands,
        )
    except ValueError as exc:
        # Geometry-level rejections (degenerate polygon, bad speed, fully
        # blocked field, ...) are client errors, not server faults.
        raise HTTPException(400, str(exc))


@router.post("/plan_auto")
def plan_auto(req: AutoCoverageRequest):
    """Plan coverage with keepouts auto-fetched from OSM around the field.

    Zone search radius = the farthest field vertex from the vertex centroid
    + 500 m margin (spray drift + zones just past the boundary), capped at
    5 km. NOT the bbox half-diagonal: that radius is only valid from the
    bbox center, and measured from the vertex centroid it can leave part of
    the field outside the queried disc — zones there would silently never
    become keepouts.

    Per-kind buffers are applied conservatively: every keepout is clipped
    with the LARGEST buffer among the kinds actually present. A single
    scalar keeps the geometry API simple, and over-standoff errs on the
    side of not spraying — never the reverse (a tree buffered like water
    wastes a little chemical; the opposite would contaminate the pond).

    KNOWN CONSEQUENCE (accepted, not a bug): powerline_buffer defaults
    wider than the rest, so when a power line is anywhere in the fetch
    radius, water/tree/building keepouts get buffered at the powerline
    distance too for that request. Over-conservative, never unsafe, and
    consistent with the largest-buffer-wins choice above. The fix, if it
    ever costs real acreage, is per-ring buffers in plan_coverage's
    clipping step — a contained refactor, deliberately not done here.

    FAIL-CLOSED: if the zone lookup fails (Overpass down / rate-limited /
    query timed out), the request is refused with a 502 naming the problem —
    a normal-looking plan with zero keepouts must never be the silent
    default. The operator can explicitly accept flying without zone data by
    passing allow_missing_zones=true, which returns the degraded plan
    flagged zones_unavailable=true (caller keepouts still applied).
    """
    _check_speed(req.speed)  # before fetch_zones: fail fast, no network
    poly = [p.model_dump() for p in req.polygon]
    clat = sum(p["lat"] for p in poly) / len(poly)
    clon = sum(p["lon"] for p in poly) / len(poly)
    m_per_deg = math.pi / 180.0 * EARTH_RADIUS_M
    kx = m_per_deg * math.cos(math.radians(clat))
    span = max(math.hypot((p["lat"] - clat) * m_per_deg,
                          (p["lon"] - clon) * kx) for p in poly)
    radius = min(span + 500.0, 5000.0)

    user_keepouts = ([[p.model_dump() for p in kp] for kp in req.keepouts]
                     if req.keepouts else [])
    plan_kwargs = dict(swath_m=req.swath, alt_m=req.alt,
                       angle_deg=req.angle, speed_ms=req.speed,
                       max_bank_deg=req.max_bank, headlands=req.headlands)
    try:
        zones = fetch_zones(clat, clon, radius)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except RuntimeError as exc:
        if not req.allow_missing_zones:
            raise HTTPException(502, {
                "message": "no-spray zone lookup failed — refusing to plan "
                           "without zone data",
                "error": "zones_unavailable",
                "detail": str(exc),
                "hint": "retry, or pass allow_missing_zones=true to plan "
                        "anyway WITHOUT water/tree/building keepouts",
            })
        try:
            degraded = plan_coverage(
                poly, **plan_kwargs,
                keepouts=(user_keepouts or None),
                keepout_buffer_m=(req.water_buffer if user_keepouts else 0.0))
        except ValueError as exc2:
            raise HTTPException(400, str(exc2))
        return {**degraded, "zones": {}, "zones_unavailable": True}

    buffers = {"water": req.water_buffer, "trees": req.tree_buffer,
               "buildings": req.building_buffer,
               "powerline": req.powerline_buffer}
    keepouts: list[list[dict]] = list(user_keepouts)
    # Hazard rings are ALSO keepouts (their spray passes still get clipped);
    # this list additionally makes connecting legs route AROUND them.
    hazards: list[list[dict]] = []
    max_buffer = req.water_buffer if user_keepouts else 0.0
    for kind in ("water", "trees", "buildings", "powerline"):
        for zone in zones.get(kind, []):
            ring = zone["coords"]
            if len(ring) > 1 and ring[0] == ring[-1]:
                ring = ring[:-1]  # planner wants open rings; zones are closed
            if len(ring) >= 3:
                keepouts.append(ring)
                max_buffer = max(max_buffer, buffers[kind])
                if kind in _HAZARD_KINDS:
                    hazards.append(ring)
    try:
        planned = plan_coverage(poly, **plan_kwargs,
                                keepouts=keepouts,
                                keepout_buffer_m=max_buffer,
                                hazards=(hazards or None),
                                hazard_buffer_m=req.powerline_buffer)
    except ValueError as exc:
        # Includes "field fully blocked by keepout zones" — a geometry
        # outcome the client must see, not a server fault.
        raise HTTPException(400, str(exc))
    crossings = planned.get("stats", {}).get("hazard_overflights", 0)
    if crossings and not req.allow_hazard_crossings:
        raise HTTPException(409, {
            "message": f"{crossings} connecting leg(s) cross a powerline "
                       "corridor and could not be routed around",
            "error": "hazard_crossings",
            "count": crossings,
            "hint": "split the field along the line, or pass "
                    "allow_hazard_crossings=true to plan anyway — those legs "
                    "fly THROUGH the corridor and must be flown manually or "
                    "at a safe crossing altitude",
        })
    # Echo the zones so a UI can render what was avoided and why.
    return {**planned, "zones": zones, "zones_unavailable": False}

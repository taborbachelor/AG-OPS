import math

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.field_boundaries import fetch_fields, fields_in_area, ring_acres, snap_to_field

router = APIRouter()


class AreaLatLon(BaseModel):
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)


class AreaBody(BaseModel):
    polygon: list[AreaLatLon] = Field(min_length=3, max_length=200)


def _clean_ring(coords):
    """Drop the ring-closing duplicate and downsample to stay editable and
    under the planner's 500-vertex cap."""
    ring = coords[:-1] if len(coords) > 1 and coords[0] == coords[-1] else list(coords)
    if len(ring) > 400:
        step = (len(ring) // 400) + 1
        ring = ring[::step]
    return ring


@router.post("/detect")
def detect_fields(body: AreaBody):
    """Auto-detect mapped ag parcels inside a selection area. found=0 is a
    normal outcome in sparsely-mapped country — operators add fields by hand."""
    selection = [{"lat": p.lat, "lon": p.lon} for p in body.polygon]
    if not all(math.isfinite(p["lat"]) and math.isfinite(p["lon"]) for p in selection):
        raise HTTPException(422, "selection coordinates must be finite")
    try:
        parcels = fields_in_area(selection)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    fields = []
    for f in parcels:
        ring = _clean_ring(f["coords"])
        if len(ring) < 3:
            continue
        fields.append({
            "polygon": ring,
            "acres": round(ring_acres(f["coords"]), 2),
            "tags": f.get("tags", {}),
        })
    return {"found": len(fields), "fields": fields}


@router.get("/")
def list_fields(lat: float, lon: float, radius: float = 1500):
    """Mapped agricultural parcels around a point (for overlays/debugging)."""
    try:
        fields = fetch_fields(lat, lon, radius)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    return {"fields": fields, "count": len(fields)}


@router.get("/snap")
def snap(lat: float, lon: float, radius: float = 1500):
    """Snap a map click to the mapped field boundary containing (or nearest to)
    it. 'found: false' is a NORMAL outcome in sparsely-mapped rural areas —
    the UI falls back to manual drawing."""
    try:
        field = snap_to_field(lat, lon, radius)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    if field is None:
        return {"found": False}
    # Drop the ring-closing duplicate vertex; downsample huge rings so the
    # result stays editable and under the planner's 500-vertex cap.
    coords = field["coords"][:-1]
    if len(coords) > 400:
        step = (len(coords) // 400) + 1
        coords = coords[::step]
    return {"found": True, "polygon": coords, "tags": field.get("tags", {})}

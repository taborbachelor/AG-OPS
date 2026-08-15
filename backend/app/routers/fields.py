import math

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.cdl import detect_fields_cdl
from app.field_boundaries import (fetch_fields, fields_in_area, ring_acres,
                                  simplify_ring, snap_to_field)

router = APIRouter()


class AreaLatLon(BaseModel):
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)


class AreaBody(BaseModel):
    polygon: list[AreaLatLon] = Field(min_length=3, max_length=200)


def _clean_ring(coords):
    """Drop the ring-closing duplicate and simplify (deviation-bounded, NOT a
    plain stride — a stride bulges outward across concavities, enclosing the
    farmstead/pond a notch excluded) to stay under the planner's vertex cap."""
    return simplify_ring(coords)


@router.post("/detect")
def detect_fields(body: AreaBody):
    """Auto-detect the fields inside a selection area.

    Primary: USDA Cropland Data Layer — satellite-derived 30m land-cover for
    the whole US. The software traces polygons around actual cropland pixels
    and skips water/trees/developed cover by classification. Fallback when
    CropScape is unreachable (or outside the US): OSM mapped parcels."""
    selection = [{"lat": p.lat, "lon": p.lon} for p in body.polygon]
    if not all(math.isfinite(p["lat"]) and math.isfinite(p["lon"]) for p in selection):
        raise HTTPException(422, "selection coordinates must be finite")

    cdl_error = None
    try:
        cdl = detect_fields_cdl(selection)
        fields = [{
            "polygon": f["polygon"],
            "holes": f.get("holes", []),   # interior non-crop islands -> keepouts
            "acres": f["acres"],
            "tags": {"source": "usda-cdl", "crop": f["crop"], "year": cdl["year"]},
        } for f in cdl["fields"]]
        return {"found": len(fields), "fields": fields,
                "source": "usda-cdl", "year": cdl["year"],
                "truncated": cdl.get("truncated", False)}
    except ValueError as e:
        raise HTTPException(422, str(e))
    except RuntimeError as e:
        cdl_error = str(e)  # CropScape unavailable — fall back to mapped parcels

    try:
        parcels, truncated = fields_in_area(selection)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except RuntimeError as e:
        raise HTTPException(502, f"Both CDL and OSM lookups failed: {e}")
    fields = []
    for f in parcels:
        ring = _clean_ring(f["coords"])
        if len(ring) < 3:
            continue
        fields.append({
            "polygon": ring,
            # OSM parcels carry NO interior-hole data (unlike CDL): an
            # explicit empty list + the cdl_unavailable flag below, so the
            # UI can warn that in-field ponds/farmsteads were NOT detected
            # instead of silently planning without them.
            "holes": [],
            "acres": round(ring_acres(f["coords"]), 2),
            "tags": {**f.get("tags", {}), "source": "osm"},
        })
    return {"found": len(fields), "fields": fields, "source": "osm",
            "truncated": truncated,
            "cdl_unavailable": True, "cdl_error": cdl_error}


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
    # Deviation-bounded simplification (see _clean_ring) — never a stride.
    coords = simplify_ring(field["coords"])
    return {"found": True, "polygon": coords, "tags": field.get("tags", {})}

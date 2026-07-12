from fastapi import APIRouter, HTTPException
from app.field_boundaries import fetch_fields, snap_to_field

router = APIRouter()


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

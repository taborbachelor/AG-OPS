"""No-spray zone lookup endpoint (OSM/Overpass-backed).

Not registered in main.py yet — wiring happens with the buffer logic.
"""

from fastapi import APIRouter, HTTPException

from app.gis_zones import fetch_zones

router = APIRouter()


@router.get("/")
def get_zones(lat: float, lon: float, radius: float = 2000):
    """Return water/trees/buildings zones around a point, with counts.

    Plain sync def so tests can call it directly without an event loop;
    the Overpass call is blocking anyway (stdlib urllib).
    """
    try:
        zones = fetch_zones(lat, lon, radius)
    except ValueError as exc:
        # Bad client input (pydantic accepts radius=nan/-1 as a float, so
        # we validate in fetch_zones) -> 422, same as pydantic would give.
        raise HTTPException(422, str(exc))
    except RuntimeError as exc:
        # Upstream (Overpass) failure, not a client error -> 502.
        raise HTTPException(502, str(exc))
    return {
        **zones,
        "counts": {kind: len(zones[kind])
                   for kind in ("water", "trees", "buildings")},
    }

"""Live keepout proximity: how close is the aircraft ACTUALLY flying to the
zones the planner avoided on paper?

`coverage.py` clips spray passes around keepouts and `reroute.py` routes
connecting legs around hazards, but both are PLANNING-time. Nothing checked the
flown position against those rings in real time — so a plan that avoids a
powerline by 20 m tells you nothing once wind, a mode change, or an operator
stick input pushes the aircraft off the planned path. This is the second layer.

Two kinds, deliberately treated differently (mirroring the split
`reroute.py` uses):
  - HAZARDS (powerlines today) protect the AIRFRAME. Flying into one is a
    crash, so proximity is a warning.
  - Ordinary keepouts (water/trees/buildings) protect SPRAY QUALITY. Overflying
    a pond with the sprayer off costs nothing, and warning about it every pass
    would train the operator to ignore the annunciator. Distance is measured
    and reported for the debrief, but it does not warn.

Pure geometry with no vehicle or network dependency, so it unit-tests with
plain dicts. The distance primitive is imported from gis_zones rather than
reimplemented — two copies of point-in-polygon that disagree is exactly the
kind of bug that ends with a wire strike.
"""

import math

from app.gis_zones import dist_to_zone_m

# Kinds whose rings are airframe hazards rather than spray-quality zones.
HAZARD_KINDS = frozenset({"powerline"})

# Ceiling on monitored rings. The guardian ticks at 1 Hz and every tick walks
# these, so this bounds worst-case per-tick work. Over the cap we keep the
# rings NEAREST the mission and say so — never a silent truncation, because a
# dropped ring is an unwatched hazard.
MAX_RINGS = 400

_M_PER_DEG_LAT = 111_320.0


def _bbox(coords: list[dict]) -> tuple:
    lats = [c["lat"] for c in coords]
    lons = [c["lon"] for c in coords]
    return (min(lats), max(lats), min(lons), max(lons))


def _bbox_dist_m(bbox: tuple, lat: float, lon: float) -> float:
    """Cheap lower bound on the distance from a point to a ring's bbox.

    Used only to SKIP rings that cannot possibly be within the buffer, so it
    must never over-estimate. Equirectangular, same approximation gis_zones
    uses at field scale.
    """
    lat_lo, lat_hi, lon_lo, lon_hi = bbox
    dlat = max(lat_lo - lat, 0.0, lat - lat_hi) * _M_PER_DEG_LAT
    kx = _M_PER_DEG_LAT * math.cos(math.radians(lat))
    dlon = max(lon_lo - lon, 0.0, lon - lon_hi) * kx
    return math.hypot(dlat, dlon)


def prepare(zones, hazard_buffer_m: float = 20.0) -> dict:
    """Turn planner zones into a monitor-ready ring set.

    `zones` accepts either the coverage response shape
    ({"water": [zone], "powerline": [zone], ...}) or a flat list of zones,
    where a zone is {"kind": str, "coords": [{"lat","lon"}, ...]}.

    Raises ValueError on malformed geometry — refusing to arm the monitor with
    junk beats monitoring against rings that are silently wrong.
    """
    if not (math.isfinite(hazard_buffer_m) and hazard_buffer_m >= 0):
        raise ValueError("hazard_buffer_m must be a finite value >= 0")

    flat = []
    if isinstance(zones, dict):
        for kind, items in zones.items():
            if not isinstance(items, list):
                continue  # "source"/flags live in the same dict
            for z in items:
                if isinstance(z, dict):
                    flat.append({**z, "kind": z.get("kind") or kind})
    elif isinstance(zones, list):
        flat = [z for z in zones if isinstance(z, dict)]
    else:
        raise ValueError("zones must be a dict of kind -> [zone] or a list")

    rings = []
    for i, z in enumerate(flat):
        coords = z.get("coords") or []
        clean = []
        for p in coords:
            try:
                lat, lon = float(p["lat"]), float(p["lon"])
            except (TypeError, KeyError, ValueError):
                raise ValueError(f"zone {i}: vertices must be {{lat, lon}}")
            if not (math.isfinite(lat) and math.isfinite(lon)
                    and -90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError(f"zone {i}: coordinate out of range")
            clean.append({"lat": lat, "lon": lon})
        if len(clean) < 3:
            continue  # a corridor ring is closed and >= 3; anything less is noise
        kind = str(z.get("kind") or "unknown")
        rings.append({"kind": kind, "coords": clean, "bbox": _bbox(clean),
                      "hazard": kind in HAZARD_KINDS})

    dropped = 0
    if len(rings) > MAX_RINGS:
        # Hazards are never the ones dropped.
        rings.sort(key=lambda r: (not r["hazard"], len(r["coords"])))
        dropped = len(rings) - MAX_RINGS
        rings = rings[:MAX_RINGS]

    return {
        "rings": rings,
        "hazard_buffer_m": float(hazard_buffer_m),
        "n_hazards": sum(1 for r in rings if r["hazard"]),
        "n_keepouts": sum(1 for r in rings if not r["hazard"]),
        "dropped": dropped,
    }


def nearest(prepared: dict, lat: float, lon: float) -> dict:
    """Distance from a position to the nearest hazard and nearest keepout.

    Returns {"known", "hazard_dist_m", "hazard_kind", "keepout_dist_m",
    "breach"}. Distances are None when there is nothing of that kind to
    measure against — NOT 0 and not infinity, so a caller can tell "clear" from
    "nothing to compare with".
    """
    buffer_m = (prepared or {}).get("hazard_buffer_m", 0.0)
    out = {"known": False, "hazard_dist_m": None, "hazard_kind": None,
           "keepout_dist_m": None, "breach": False, "buffer_m": buffer_m}
    if not prepared or not prepared.get("rings"):
        return out
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return out
    out["known"] = True

    pt = {"lat": lat, "lon": lon}
    best_haz, best_haz_kind, best_keep = None, None, None

    for ring in prepared["rings"]:
        current = best_haz if ring["hazard"] else best_keep
        # Skip any ring whose bbox is already farther than the best distance
        # found so far for its kind — it cannot lower the minimum. The bbox
        # bound never over-estimates, so this is exact, not a heuristic.
        if current is not None and _bbox_dist_m(ring["bbox"], lat, lon) >= current:
            continue
        d = dist_to_zone_m(pt, ring)
        if ring["hazard"]:
            if best_haz is None or d < best_haz:
                best_haz, best_haz_kind = d, ring["kind"]
        else:
            if best_keep is None or d < best_keep:
                best_keep = d

    out["hazard_dist_m"] = best_haz
    out["hazard_kind"] = best_haz_kind
    out["keepout_dist_m"] = best_keep
    out["breach"] = best_haz is not None and best_haz < buffer_m
    return out

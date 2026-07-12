"""Field-boundary lookup for snap-to-field selection.

Fetches mapped agricultural parcels (OSM landuse=farmland / meadow / farm /
orchard / vineyard) around a point so the UI can snap a single click to a real
field perimeter instead of hand-tracing it. Coverage in rural areas varies —
callers must treat "no field found" as a normal outcome and fall back to
manual drawing.

Self-contained Overpass client (stdlib urllib) mirroring gis_zones' behavior:
25s timeout, one retry, in-memory TTL cache, radius capped at 5 km.
"""

import json
import math
import time
import urllib.parse
import urllib.request

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "rc-plane-gcs/1.0"
MAX_RADIUS_M = 5000.0
CACHE_TTL_S = 600.0
CACHE_MAX_ENTRIES = 64

_cache: dict = {}

_QUERY_TMPL = """
[out:json][timeout:25];
(
  way["landuse"~"^(farmland|meadow|farm|orchard|vineyard)$"](around:{radius},{lat},{lon});
  relation["landuse"~"^(farmland|meadow|farm|orchard|vineyard)$"](around:{radius},{lat},{lon});
);
out geom;
"""


def _overpass(query: str) -> dict:
    data = urllib.parse.urlencode({"data": query}).encode()
    last_err = None
    for _ in range(2):  # one try + one retry
        try:
            req = urllib.request.Request(
                OVERPASS_URL, data=data, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=25) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:  # noqa: BLE001 — network layer, retried once
            last_err = e
    raise RuntimeError(f"Overpass field lookup failed: {last_err}")


def _ring_from_geometry(geom) -> list:
    """Overpass 'out geom' way geometry -> closed [{lat,lon}] ring, or []."""
    if not geom or len(geom) < 4:
        return []
    ring = [{"lat": g["lat"], "lon": g["lon"]} for g in geom]
    if ring[0] != ring[-1]:
        ring.append(dict(ring[0]))
    return ring


def _parse_fields(payload: dict) -> list:
    fields = []
    for el in payload.get("elements", []):
        tags = el.get("tags", {}) or {}
        if el.get("type") == "way":
            ring = _ring_from_geometry(el.get("geometry"))
            if ring:
                fields.append({"coords": ring, "tags": tags})
        elif el.get("type") == "relation":
            # Use outer members that carry geometry; skip otherwise.
            for m in el.get("members", []):
                if m.get("role") == "outer":
                    ring = _ring_from_geometry(m.get("geometry"))
                    if ring:
                        fields.append({"coords": ring, "tags": tags})
    return fields


def _prune_cache(now: float):
    expired = [k for k, (ts, _) in _cache.items() if now - ts > CACHE_TTL_S]
    for k in expired:
        del _cache[k]
    while len(_cache) > CACHE_MAX_ENTRIES:
        _cache.pop(next(iter(_cache)))


def fetch_fields(lat: float, lon: float, radius_m: float = 1500.0) -> list:
    """Mapped agricultural parcels around a point. Raises ValueError on bad
    radius, RuntimeError when Overpass is unreachable."""
    if not (math.isfinite(radius_m) and radius_m > 0):
        raise ValueError("radius must be a positive finite number")
    radius_m = min(radius_m, MAX_RADIUS_M)

    now = time.time()
    _prune_cache(now)
    key = (round(lat, 3), round(lon, 3), int(radius_m))
    hit = _cache.get(key)
    if hit and now - hit[0] <= CACHE_TTL_S:
        return hit[1]

    payload = _overpass(_QUERY_TMPL.format(radius=int(radius_m), lat=lat, lon=lon))
    fields = _parse_fields(payload)
    _cache[key] = (now, fields)
    return fields


def point_in_ring(lat: float, lon: float, ring: list) -> bool:
    """Ray-cast point-in-polygon on a [{lat,lon}] ring."""
    inside = False
    n = len(ring)
    for i in range(n - 1):  # ring is closed; last point repeats the first
        y1, x1 = ring[i]["lat"], ring[i]["lon"]
        y2, x2 = ring[i + 1]["lat"], ring[i + 1]["lon"]
        if (y1 > lat) != (y2 > lat):
            x_cross = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < x_cross:
                inside = not inside
    return inside


def ring_acres(ring: list) -> float:
    """Shoelace area of a [{lat,lon}] ring in acres (equirectangular)."""
    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    if len(pts) < 3:
        return 0.0
    clat = sum(p["lat"] for p in pts) / len(pts)
    kx = 111320.0 * math.cos(math.radians(clat))
    ky = 111320.0
    area = 0.0
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        area += (a["lon"] * kx) * (b["lat"] * ky) - (b["lon"] * kx) * (a["lat"] * ky)
    return abs(area / 2.0) / 4046.8564224


def fields_in_area(selection: list, cap: int = 40) -> list:
    """All mapped ag parcels inside a selection polygon: parcel centroid (or
    any sampled vertex) inside the selection counts. Selection drives the
    Overpass radius, capped like everything else."""
    if len(selection) < 3:
        raise ValueError("selection polygon needs at least 3 vertices")
    clat = sum(p["lat"] for p in selection) / len(selection)
    clon = sum(p["lon"] for p in selection) / len(selection)
    kx = 111320.0 * math.cos(math.radians(clat))
    span = max(math.hypot((p["lat"] - clat) * 111320.0, (p["lon"] - clon) * kx)
               for p in selection)
    radius = min(MAX_RADIUS_M, span + 300.0)

    sel_ring = list(selection)
    if sel_ring[0] != sel_ring[-1]:
        sel_ring = sel_ring + [sel_ring[0]]

    parcels = fetch_fields(clat, clon, radius)
    out = []
    for f in parcels:
        cs = f["coords"]
        pc_lat = sum(c["lat"] for c in cs) / len(cs)
        pc_lon = sum(c["lon"] for c in cs) / len(cs)
        inside = point_in_ring(pc_lat, pc_lon, sel_ring)
        if not inside:
            step = max(1, len(cs) // 10)
            inside = any(point_in_ring(c["lat"], c["lon"], sel_ring) for c in cs[::step])
        if inside:
            out.append(f)
        if len(out) >= cap:
            break
    return out


def snap_to_field(lat: float, lon: float, radius_m: float = 1500.0):
    """The parcel containing the click, else the one whose centroid is
    nearest within 300 m, else None."""
    fields = fetch_fields(lat, lon, radius_m)
    for f in fields:
        if point_in_ring(lat, lon, f["coords"]):
            return f
    best, best_d = None, 300.0  # meters
    kx = 111320.0 * math.cos(math.radians(lat))
    for f in fields:
        cs = f["coords"]
        clat = sum(c["lat"] for c in cs) / len(cs)
        clon = sum(c["lon"] for c in cs) / len(cs)
        d = math.hypot((clat - lat) * 111320.0, (clon - lon) * kx)
        if d < best_d:
            best, best_d = f, d
    return best

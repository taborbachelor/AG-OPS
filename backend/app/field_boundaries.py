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

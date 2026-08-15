"""GIS no-spray zones from OpenStreetMap (Overpass API).

Pulls water bodies, tree cover, and buildings around a point so the
mission planner can carve out no-spray buffers. Everything here is
stdlib-only (urllib) so the backend keeps its tiny dependency footprint.

A "zone" is a closed polygon ring in the shape:

    {"kind": "water"|"trees"|"buildings",
     "coords": [{"lat": ..., "lon": ...}, ...],   # first point == last point
     "tags": {...}}                               # raw OSM tags for the UI
"""

import json
import math
import time
import urllib.parse
import urllib.request

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "rc-plane-gcs/1.0"
# Client timeout must exceed the server-side [timeout:25] budget, or a query
# running near the server limit reliably dies client-side first and burns
# both attempts on work the server would have finished.
REQUEST_TIMEOUT_S = 30
# Refuse absurd response bodies before parsing them into memory (the cache
# would then pin multi-MB geometry sets).
MAX_RESPONSE_BYTES = 30_000_000

# Overpass is a shared free service: cap the query area and cache results
# so a user panning the map doesn't hammer it (or get us rate-limited).
MAX_RADIUS_M = 5000
CACHE_TTL_S = 600  # 10 minutes

# OSM tag -> zone kind. Buildings are handled separately (ways only),
# because building relations are rare and their geometry is messy.
_KIND_TAGS = {
    "water": [("natural", "water"), ("landuse", "reservoir"), ("natural", "wetland")],
    "trees": [("natural", "wood"), ("landuse", "forest"), ("landuse", "orchard")],
}

# Cache keyed by (round(lat,3), round(lon,3), int(radius_m)); ~100 m grid,
# coarse enough that tiny GPS jitter between requests still hits the cache.
# Bounded: expired entries are swept on every fetch and the oldest are
# dropped past _CACHE_MAX_ENTRIES, so a long session panning across the
# map can't grow memory without limit (each entry holds full polygon
# geometry, which adds up fast).
_CACHE_MAX_ENTRIES = 128
_cache: dict[tuple, tuple[float, dict]] = {}


def _prune_cache(now: float) -> None:
    """Evict stale and excess cache entries.

    TTL used to be checked only on read, so entries for coordinates never
    revisited lived forever. Sweep expired keys on every fetch, then drop
    oldest-inserted entries (dicts iterate in insertion order) until we
    are below the size cap — simple FIFO eviction is plenty here.
    """
    for key in [k for k, (ts, _) in _cache.items() if now - ts >= CACHE_TTL_S]:
        del _cache[key]
    while len(_cache) >= _CACHE_MAX_ENTRIES:
        del _cache[next(iter(_cache))]


# Linear waterways (streams, drainage ditches, drains, rivers, canals) are
# the single most common ag no-spray feature and are mapped as OSM ways
# (lines), not polygons. They are turned into zero-area "corridor" rings
# (the polyline traced out and back) — the planner's Minkowski buffer then
# produces a keepout corridor of the configured water buffer around them.
_WATERWAY_RE = "^(stream|ditch|drain|river|canal|riverbank)$"


def _build_query(lat: float, lon: float, radius_m: float) -> str:
    """Build the Overpass QL query for all three zone kinds at once."""
    around = f"(around:{int(radius_m)},{lat},{lon})"
    parts = []
    for pairs in _KIND_TAGS.values():
        for key, val in pairs:
            # Ways and relations: lakes/forests are frequently multipolygons.
            parts.append(f'way["{key}"="{val}"]{around};')
            parts.append(f'relation["{key}"="{val}"]{around};')
    # Buildings: ways only (see _KIND_TAGS comment).
    parts.append(f'way["building"]{around};')
    # Linear waterways (see _WATERWAY_RE above).
    parts.append(f'way["waterway"~"{_WATERWAY_RE}"]{around};')
    body = "".join(parts)
    # 'out geom;' inlines per-node coordinates so we never need a second
    # request to resolve node references.
    return f"[out:json][timeout:25];({body});out geom;"


def _overpass_post(query: str) -> dict:
    """POST a query to Overpass and return the decoded JSON.

    Retries exactly once (the public endpoint sheds load with transient
    504s) and raises RuntimeError with a clear message after that so the
    router can map it to a 502.
    """
    data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    last_err: Exception | None = None
    for _attempt in range(2):  # initial try + exactly one retry
        try:
            req = urllib.request.Request(
                OVERPASS_URL, data=data, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
                body = resp.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise RuntimeError("Overpass response too large")
                payload = json.loads(body.decode("utf-8"))
            # CRITICAL: Overpass reports its own failures (query timeout,
            # memory limit) as HTTP 200 with a "remark" and empty/PARTIAL
            # elements. Treating that as "no zones here" would return a
            # confident all-clear for an area that has water — the exact
            # silent fail-open this module must never produce.
            remark = str(payload.get("remark") or "")
            if "error" in remark.lower() or "timed out" in remark.lower():
                raise RuntimeError(f"Overpass runtime failure: {remark[:200]}")
            return payload
        except Exception as exc:  # noqa: BLE001 - URLError, timeout, bad JSON...
            last_err = exc
    raise RuntimeError(
        f"Overpass API request failed after retry: {last_err}")


def _close_ring(points: list[dict]) -> list[dict] | None:
    """Return a closed ring (first == last), or None if degenerate.

    OSM ways are open point sequences; a polygon ring needs at least a
    closed triangle (4 points incl. the repeated first), anything smaller
    can't enclose area and is useless for buffer math.
    """
    coords = [{"lat": p["lat"], "lon": p["lon"]} for p in points]
    if not coords:
        return None
    if coords[0] != coords[-1]:
        coords.append(dict(coords[0]))
    if len(coords) < 4:
        return None
    return coords


def _classify(tags: dict) -> str | None:
    """Map raw OSM tags to a zone kind, or None if we don't care."""
    for kind, pairs in _KIND_TAGS.items():
        for key, val in pairs:
            if tags.get(key) == val:
                return kind
    if "waterway" in tags:
        return "water"
    if "building" in tags:
        return "buildings"
    return None


def _corridor_ring(points: list[dict]) -> list[dict] | None:
    """Turn an open polyline (waterway) into a zero-area out-and-back ring.

    The planner buffers keepouts as interior + vertex discs + edge strips
    (Minkowski sum), so a degenerate ring tracing the line forward then
    backward yields exactly a buffer_m-wide corridor around the waterway —
    no chord-closing across the field, no polygon assembly needed."""
    pts = [{"lat": p["lat"], "lon": p["lon"]} for p in points]
    if len(pts) < 2:
        return None
    if len(pts) == 2:
        # Two-point segment: insert the midpoint so the open ring still has
        # >= 3 distinct vertices after callers strip the closing duplicate
        # (a bare [a, b] would be dropped by their len >= 3 check).
        mid = {"lat": (pts[0]["lat"] + pts[1]["lat"]) / 2.0,
               "lon": (pts[0]["lon"] + pts[1]["lon"]) / 2.0}
        pts = [pts[0], mid, pts[1]]
    ring = pts + pts[-2::-1]  # out and back; first == last
    return ring


def _stitch_outer_rings(members: list) -> list[list[dict]]:
    """Assemble a relation's outer ways into closed rings.

    A lake/forest outer boundary is frequently split across several ways;
    chord-closing each way separately leaves interior area of a concave
    body OUTSIDE every ring (sprayable water). Stitch ways end-to-end by
    matching endpoints; segments that don't stitch into a closed ring fall
    back to per-segment chord closing (the old behavior)."""
    segs = []
    for m in members:
        if m.get("role") != "outer" or "geometry" not in m:
            continue
        pts = [{"lat": p["lat"], "lon": p["lon"]} for p in m["geometry"]]
        if len(pts) >= 2:
            segs.append(pts)

    def key(p):
        return (round(p["lat"], 7), round(p["lon"], 7))

    rings: list[list[dict]] = []
    while segs:
        chain = segs.pop()
        # Extend the chain until it closes or nothing joins.
        extended = True
        while extended and key(chain[0]) != key(chain[-1]):
            extended = False
            for i, s in enumerate(segs):
                if key(s[0]) == key(chain[-1]):
                    chain = chain + s[1:]
                elif key(s[-1]) == key(chain[-1]):
                    chain = chain + s[-2::-1]
                elif key(s[-1]) == key(chain[0]):
                    chain = s + chain[1:]
                elif key(s[0]) == key(chain[0]):
                    chain = s[::-1] + chain[1:]
                else:
                    continue
                segs.pop(i)
                extended = True
                break
        rings.append(chain)
    return rings


def _parse_overpass(payload: dict) -> dict:
    """Turn an Overpass 'out geom' response into kind-bucketed zones."""
    zones: dict[str, list] = {"water": [], "trees": [], "buildings": []}
    for el in payload.get("elements", []):
        tags = el.get("tags") or {}  # explicit "tags": null happens in the wild
        kind = _classify(tags)
        if kind is None:
            continue
        if el.get("type") == "way":
            geom = el.get("geometry") or []
            if "waterway" in tags and not (
                    len(geom) > 1 and geom[0] == geom[-1]):
                # Linear waterway: corridor ring instead of chord-closing.
                ring = _corridor_ring(geom)
            else:
                ring = _close_ring(geom)
            if ring:
                zones[kind].append({"kind": kind, "coords": ring, "tags": tags})
        elif el.get("type") == "relation" and kind != "buildings":
            for chain in _stitch_outer_rings(el.get("members", [])):
                ring = _close_ring(chain)
                if ring:
                    zones[kind].append(
                        {"kind": kind, "coords": ring, "tags": tags})
    return zones


def fetch_zones(lat: float, lon: float, radius_m: float = 2000) -> dict:
    """Fetch no-spray zones (water/trees/buildings) around a point.

    Returns {"water": [zone], "trees": [zone], "buildings": [zone],
    "source": "overpass"}. Radius is capped at MAX_RADIUS_M and results
    are cached for CACHE_TTL_S. Raises ValueError for a non-finite or
    non-positive radius (client error) and RuntimeError if Overpass is
    unreachable after one retry (upstream error).
    """
    radius_m = float(radius_m)
    # Reject NaN/inf/<=0 before they reach int() below (int(nan) raises)
    # or get baked into an Overpass query: a negative 'around:' radius
    # just burns the retry budget on a request that can never succeed.
    if not math.isfinite(radius_m) or radius_m <= 0:
        raise ValueError("radius_m must be a positive finite number")
    # lat/lon must be validated here too: a NaN center produces an Overpass
    # query that returns 200 with zero elements — which would be cached and
    # served as a confident "no zones here" under a key nothing can evict.
    lat, lon = float(lat), float(lon)
    if not (math.isfinite(lat) and math.isfinite(lon)
            and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("lat/lon must be finite coordinates in range")
    radius_m = min(radius_m, MAX_RADIUS_M)
    # 4 decimals (~11 m grid): coarse enough for GPS jitter to hit the cache,
    # fine enough that a hit is never a disc offset ~78 m from the request
    # (which would drop zones near the radius edge).
    key = (round(lat, 4), round(lon, 4), int(radius_m))
    now = time.time()
    _prune_cache(now)
    cached = _cache.get(key)
    if cached is not None and now - cached[0] < CACHE_TTL_S:
        return cached[1]

    payload = _overpass_post(_build_query(lat, lon, radius_m))
    result = _parse_overpass(payload)
    result["source"] = "overpass"
    _cache[key] = (now, result)
    return result


# --- Geometry helpers (equirectangular approximation) -----------------------
# At field scale (<= 5 km) the flat-earth error is centimetres, which is
# well inside GPS noise, so we avoid pulling in a geodesy dependency.

_M_PER_DEG_LAT = 111_320.0


def _project(pt: dict, ref_lat_rad: float, ref: dict) -> tuple[float, float]:
    """Project a lat/lon point to local (x, y) meters around `ref`."""
    x = (pt["lon"] - ref["lon"]) * _M_PER_DEG_LAT * math.cos(ref_lat_rad)
    y = (pt["lat"] - ref["lat"]) * _M_PER_DEG_LAT
    return x, y


def _point_in_ring(x: float, y: float, ring: list[tuple[float, float]]) -> bool:
    """Standard ray-cast point-in-polygon test on projected coords."""
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y):
            # x of the edge at height y; crossing to the right toggles.
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def _dist_to_segment(px: float, py: float,
                     ax: float, ay: float, bx: float, by: float) -> float:
    """Distance from point p to segment a-b in meters."""
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0.0:  # degenerate segment
        return math.hypot(px - ax, py - ay)
    # Clamp the projection parameter so we measure to the segment, not
    # the infinite line.
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def dist_to_zone_m(pt: dict, zone: dict) -> float:
    """Distance in meters from `pt` ({lat, lon}) to a zone polygon.

    Returns 0.0 if the point lies inside the zone. This is the primitive
    for future no-spray buffer logic: a spray point is legal iff
    dist_to_zone_m(pt, zone) >= buffer_m for every nearby zone, so
    "inside == 0" makes the comparison uniform (inside always violates
    any positive buffer).

    Uses a ray-cast point-in-polygon test plus minimum point-to-segment
    distance over the ring edges, all in a local equirectangular
    projection centred on `pt`.
    """
    coords = zone["coords"]
    ref_lat_rad = math.radians(pt["lat"])
    ring = [_project(c, ref_lat_rad, pt) for c in coords]
    if _point_in_ring(0.0, 0.0, ring):
        return 0.0
    best = math.inf
    for i in range(len(ring) - 1):  # ring is closed: last edge is implicit
        ax, ay = ring[i]
        bx, by = ring[i + 1]
        best = min(best, _dist_to_segment(0.0, 0.0, ax, ay, bx, by))
    return best

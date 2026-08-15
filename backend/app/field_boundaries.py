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
    # 4 decimals (~11 m grid): a hit can no longer be a disc offset ~78 m
    # from the requested center (parcels near the radius edge went missing).
    key = (round(lat, 4), round(lon, 4), int(radius_m))
    hit = _cache.get(key)
    if hit and now - hit[0] <= CACHE_TTL_S:
        return hit[1]

    payload = _overpass(_QUERY_TMPL.format(radius=int(radius_m), lat=lat, lon=lon))
    fields = _parse_fields(payload)
    _cache[key] = (now, fields)
    return fields


def simplify_ring(ring: list, tol_m: float = 3.0, max_vertices: int = 450) -> list:
    """Reduce a [{lat,lon}] ring's vertex count with a BOUNDED deviation.

    Replaces the old plain-stride decimation (`ring[::step]`), which across a
    concavity moves the boundary OUTWARD — a parcel mapped with a notch cut
    around a farmstead/pond would lose the notch and enclose it, i.e. spray
    it. Douglas-Peucker bounds the deviation to tol_m in either direction
    (3 m is far below any spray buffer). If DP alone can't reach
    max_vertices, the tolerance is doubled a few times before falling back
    to a final stride pass (huge pathological rings only)."""
    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else list(ring)
    if len(pts) <= max_vertices:
        return pts
    clat = sum(p["lat"] for p in pts) / len(pts)
    kx = 111320.0 * math.cos(math.radians(clat))
    xy = [(p["lon"] * kx, p["lat"] * 111320.0) for p in pts]

    def dp(tol):
        keep = {0, len(xy) - 1}

        def rec(lo, hi):
            ax, ay = xy[lo]
            bx, by = xy[hi]
            seg = math.hypot(bx - ax, by - ay) or 1e-9
            dmax, imax = -1.0, None
            for i in range(lo + 1, hi):
                px, py = xy[i]
                d = abs((bx - ax) * (ay - py) - (ax - px) * (by - ay)) / seg
                if d > dmax:
                    dmax, imax = d, i
            if dmax > tol and imax is not None:
                rec(lo, imax)
                keep.add(imax)
                rec(imax, hi)

        rec(0, len(xy) - 1)
        return sorted(keep)

    tol = tol_m
    for _ in range(4):
        idx = dp(tol)
        if len(idx) <= max_vertices:
            return [pts[i] for i in idx]
        tol *= 2.0
    step = (len(pts) // max_vertices) + 1
    return pts[::step]


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


def fields_in_area(selection: list, cap: int = 40) -> tuple[list, bool]:
    """All mapped ag parcels inside a selection polygon: parcel centroid (or
    any sampled vertex) inside the selection counts. Selection drives the
    Overpass radius, capped like everything else.

    Returns (parcels, truncated): truncated=True means more than `cap`
    parcels matched and the list is an arbitrary subset — callers must
    surface that instead of presenting it as complete."""
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
    truncated = False
    for f in parcels:
        cs = f["coords"]
        pc_lat = sum(c["lat"] for c in cs) / len(cs)
        pc_lon = sum(c["lon"] for c in cs) / len(cs)
        inside = point_in_ring(pc_lat, pc_lon, sel_ring)
        if not inside:
            step = max(1, len(cs) // 10)
            inside = any(point_in_ring(c["lat"], c["lon"], sel_ring) for c in cs[::step])
        if inside:
            if len(out) >= cap:
                truncated = True
                break
            out.append(f)
    return out, truncated


def _dist_to_ring_m(lat: float, lon: float, ring: list) -> float:
    """Minimum distance (m) from a point to a ring's boundary edges."""
    kx = 111320.0 * math.cos(math.radians(lat))
    px, py = lon * kx, lat * 111320.0
    best = math.inf
    for i in range(len(ring) - 1):
        ax, ay = ring[i]["lon"] * kx, ring[i]["lat"] * 111320.0
        bx, by = ring[i + 1]["lon"] * kx, ring[i + 1]["lat"] * 111320.0
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        if seg2 == 0.0:
            best = min(best, math.hypot(px - ax, py - ay))
            continue
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
        best = min(best, math.hypot(px - (ax + t * dx), py - (ay + t * dy)))
    return best


def snap_to_field(lat: float, lon: float, radius_m: float = 1500.0):
    """The parcel containing the click, else the one whose BOUNDARY is
    nearest within 300 m, else None. (Nearest boundary, not nearest
    centroid: a click between two parcels must snap to the one it is
    actually beside, not to a farther, smaller parcel whose centroid
    happens to be closer.)"""
    fields = fetch_fields(lat, lon, radius_m)
    for f in fields:
        if point_in_ring(lat, lon, f["coords"]):
            return f
    best, best_d = None, 300.0  # meters
    for f in fields:
        d = _dist_to_ring_m(lat, lon, f["coords"])
        if d < best_d:
            best, best_d = f, d
    return best

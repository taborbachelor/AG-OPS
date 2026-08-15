"""Imagery-derived field auto-detection via the USDA Cropland Data Layer.

The CDL classifies every 30m pixel of the continental US by land cover
(corn, soybeans, wheat, ... water, forest, developed). Given a selection
area we: fetch the CDL clip from CropScape, keep only cropland pixels,
segment them into connected fields, trace each field's EXACT pixel-edge
boundary (plus interior non-crop holes), and hand back lat/lon polygons —
the software literally "draws around the fields".

Precision notes:
- Boundaries follow pixel EDGES (not centers), so the polygon hugs the
  classified footprint exactly — no half-pixel inset.
- Interior non-crop islands (farmsteads, ponds, tree stands) are returned as
  hole polygons so the planner can keep them out automatically.
- Single-pixel classification speckle is filled before tracing.

Powerlines are NOT in the CDL — known gap, handled separately later.

Pure stdlib + Pillow (TIFF decode). Grid sizes at 30m are tiny (a 3x3 km
selection is ~100x100 px), so plain-python processing is instant.
"""

import io
import math
import re
import time
import urllib.request

from PIL import Image

from app.albers import to_albers, from_albers

# NOTE: the service's GetCDLData operation was retired; GetCDLFile is the
# surviving raster-clip operation (verified against the live WSDL).
CROPSCAPE = "https://nassgeodata.gmu.edu/axis2/services/CDLService/GetCDLFile"
USER_AGENT = "rc-plane-gcs/1.0"
PIXEL_M = 30.0
ACRES_PER_PX = (PIXEL_M * PIXEL_M) / 4046.8564224   # ~0.2224 ac
MAX_SPAN_M = 6000.0        # selection bbox cap (keeps rasters small)
MIN_FIELD_PX = 10          # ~2.2 acres — ignore slivers
MIN_HOLE_PX = 1            # EVERY interior island becomes a keepout hole: a
                           # single 30x30 m pixel of water/farmstead is a real
                           # feature (a farm pond), not noise — dropping it
                           # put the pond inside the sprayable polygon. Extra
                           # tiny keepouts cost a little chemical; the reverse
                           # sprays the pond.
# Total wall-clock budget for a detect call (all year attempts + downloads):
# each _http_get could otherwise block a threadpool worker for 60 s x 6
# requests — the same pool that serves disarm/RTL handlers.
DETECT_BUDGET_S = 90.0
MAX_FIELDS = 40
SIMPLIFY_TOL_PX = 0.9      # straightens 30m staircases into clean diagonals
                           # (outer boundaries only — holes are keepouts and
                           # stay exact; see detect_fields_cdl)

# CDL land-cover codes. Cropland = 1..61 (row crops, grains, hay, fallow),
# 66..77 (orchards/fruit), 196..255 (double-crops & misc crops). Explicitly
# excluded by omission: water 111, developed 121-124, forest 141-143,
# shrub/barren 131/152, wetlands 190/195, grass/pasture 62/176 (opt-in).
CROP_CODES = set(range(1, 62)) | set(range(66, 78)) | set(range(196, 256))
PASTURE_CODES = {62, 176}
# Classes that are REAL physical features, never "speckle": water, developed,
# forest, wetlands. A single-pixel island of these (a farm pond, a wellhead
# pad) must never be despeckle-filled into sprayable cropland.
PROTECTED_CODES = {111, 112, 121, 122, 123, 124, 141, 142, 143, 190, 195}

CROP_NAMES = {
    1: "Corn", 4: "Sorghum", 5: "Soybeans", 6: "Sunflower", 21: "Barley",
    22: "Durum Wheat", 23: "Spring Wheat", 24: "Winter Wheat", 26: "WinWht/Soy",
    28: "Oats", 29: "Millet", 31: "Canola", 33: "Safflower", 36: "Alfalfa",
    37: "Hay", 41: "Sugarbeets", 42: "Dry Beans", 43: "Potatoes", 52: "Lentils",
    53: "Peas", 61: "Fallow", 62: "Pasture", 176: "Pasture",
}

_cache: dict = {}
_CACHE_TTL = 1800.0


def _http_get(url: str, deadline: float, timeout: float = 60.0) -> bytes:
    """GET with a per-request timeout AND a caller wall-clock deadline, so a
    slow CropScape day can't pin a threadpool worker for minutes (that pool
    is shared with disarm/RTL handlers)."""
    remaining = deadline - time.time()
    if remaining <= 1.0:
        raise RuntimeError("CDL request budget exhausted")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=min(timeout, remaining)) as resp:
        return resp.read()


def _fetch_clip(bbox_5070, year: int, deadline: float):
    """CropScape clip request -> (grid rows of class codes, geotransform).
    geotransform = (ulx, uly, sx, sy): x = ulx + px*sx, y = uly - py*sy."""
    x1, y1, x2, y2 = bbox_5070
    url = (f"{CROPSCAPE}?year={year}&"
           f"bbox={x1:.0f},{y1:.0f},{x2:.0f},{y2:.0f}")
    xml = _http_get(url, deadline).decode(errors="ignore")
    m = re.search(r"<returnURL>(.*?)</returnURL>", xml)
    if not m:
        raise RuntimeError(f"CropScape gave no raster URL (year {year}): {xml[:200]}")
    tif = _http_get(m.group(1).strip(), deadline)

    img = Image.open(io.BytesIO(tif))
    if img.mode not in ("P", "L"):
        # CDL rasters are 8-bit class codes. Anything else (an RGB error
        # tile, a float raster) would make every `grid[r][c] in codes` test
        # silently False -> "found: 0" presented as a real result.
        raise RuntimeError(f"unexpected CDL raster mode {img.mode!r}")
    w, h = img.size
    data = list(img.getdata())
    grid = [data[r * w:(r + 1) * w] for r in range(h)]

    # Prefer embedded GeoTIFF georeferencing; fall back to the requested bbox.
    tags = getattr(img, "tag_v2", {}) or {}
    if 33922 in tags and 33550 in tags:
        tp = tags[33922]           # ModelTiepoint: i,j,k, X,Y,Z
        sc = tags[33550]           # ModelPixelScale: sx, sy, sz
        gt = (float(tp[3]), float(tp[4]), float(sc[0]), float(sc[1]))
    else:
        gt = (min(x1, x2), max(y1, y2), PIXEL_M, PIXEL_M)
    return grid, gt


def _despeckle(mask, w, h, grid=None):
    """Fill single-pixel classification noise: a non-crop cell with all four
    neighbors crop becomes crop; an isolated crop cell (no crop neighbors)
    is dropped. One pass each — 30m speckle, not real features.

    EXCEPTION: cells classified as a PROTECTED code (water, developed,
    forest, wetland) are never filled — a lone 30 m water pixel is a farm
    pond, and filling it re-labels the pond as sprayable cropland."""
    fill = []
    drop = []
    for r in range(h):
        for c in range(w):
            n = [(r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)]
            inb = [(rr, cc) for rr, cc in n if 0 <= rr < h and 0 <= cc < w]
            crop_n = sum(1 for rr, cc in inb if mask[rr][cc])
            if not mask[r][c] and len(inb) == 4 and crop_n == 4:
                if grid is not None and grid[r][c] in PROTECTED_CODES:
                    continue
                fill.append((r, c))
            elif mask[r][c] and crop_n == 0:
                drop.append((r, c))
    for r, c in fill:
        mask[r][c] = True
    for r, c in drop:
        mask[r][c] = False
    return mask


def _segment(mask, w, h):
    """4-connected components over a boolean grid -> list of cell lists."""
    seen = [[False] * w for _ in range(h)]
    comps = []
    for r0 in range(h):
        for c0 in range(w):
            if not mask[r0][c0] or seen[r0][c0]:
                continue
            comp = []
            stack = [(r0, c0)]
            seen[r0][c0] = True
            while stack:
                r, c = stack.pop()
                comp.append((r, c))
                for nr, nc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                    if 0 <= nr < h and 0 <= nc < w and mask[nr][nc] and not seen[nr][nc]:
                        seen[nr][nc] = True
                        stack.append((nr, nc))
            comps.append(comp)
    return comps


def _edge_loops(comp):
    """EXACT pixel-edge outline of a cell set.

    Emits every boundary edge on the pixel-corner grid (cell (r,c) owns the
    corners (c,r)..(c+1,r+1)), directed so the component interior stays on the
    left, then chains edges into closed loops. Returns a list of corner-coord
    loops [(cx, cy), ...]: the largest is the outer boundary, the rest are
    interior holes. This is the raster's true footprint — no center-inset."""
    S = set(comp)
    edges = {}

    def add(a, b):
        edges.setdefault(a, []).append(b)

    for (r, c) in S:
        if (r - 1, c) not in S:
            add((c, r), (c + 1, r))            # top edge, walking east
        if (r, c + 1) not in S:
            add((c + 1, r), (c + 1, r + 1))    # right edge, walking south
        if (r + 1, c) not in S:
            add((c + 1, r + 1), (c, r + 1))    # bottom edge, walking west
        if (r, c - 1) not in S:
            add((c, r + 1), (c, r))            # left edge, walking north

    loops = []
    while edges:
        start = next(iter(edges))
        outs = edges[start]
        cur_edge_end = outs.pop()
        if not outs:
            del edges[start]
        loop = [start]
        prev_dir = (cur_edge_end[0] - start[0], cur_edge_end[1] - start[1])
        cur = cur_edge_end
        # Hard bound: total edges is finite; each iteration consumes one.
        for _ in range(4 * len(comp) + 8):
            if cur == start:
                break
            loop.append(cur)
            outs = edges.get(cur)
            if not outs:
                break  # malformed (shouldn't happen) — bail with what we have
            if len(outs) == 1:
                nxt = outs.pop()
            else:
                # Corner where the ring pinches (diagonal cells): take the
                # sharpest LEFT turn to stay on this ring.
                def key(o):
                    dx, dy = o[0] - cur[0], o[1] - cur[1]
                    cross = prev_dir[0] * dy - prev_dir[1] * dx
                    dot = prev_dir[0] * dx + prev_dir[1] * dy
                    return (cross, dot)
                outs.sort(key=key)
                nxt = outs.pop()
            if not edges[cur]:
                del edges[cur]
            prev_dir = (nxt[0] - cur[0], nxt[1] - cur[1])
            cur = nxt
        loops.append(loop)
    return loops


def _loop_area_px(loop):
    """|shoelace| of a corner loop, in pixels² (== cells enclosed)."""
    a = 0.0
    n = len(loop)
    for i in range(n):
        x1, y1 = loop[i]
        x2, y2 = loop[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def _collapse_collinear(pts):
    """Drop midpoints of straight runs (rectilinear outlines get tiny)."""
    if len(pts) < 3:
        return pts
    out = []
    n = len(pts)
    for i in range(n):
        px, py = pts[(i - 1) % n]
        cx, cy = pts[i]
        nx, ny = pts[(i + 1) % n]
        if (cx - px) * (ny - cy) != (cy - py) * (nx - cx):
            out.append((cx, cy))
    return out if len(out) >= 3 else pts


def _simplify(pts, tol):
    """Douglas-Peucker on [(x, y)] with tolerance in the same units."""
    if len(pts) < 3:
        return pts

    def dp(lo, hi, keep):
        ax, ay = pts[lo]
        bx, by = pts[hi]
        dmax, imax = -1.0, None
        seg = math.hypot(bx - ax, by - ay) or 1e-9
        for i in range(lo + 1, hi):
            px, py = pts[i]
            d = abs((bx - ax) * (ay - py) - (ax - px) * (by - ay)) / seg
            if d > dmax:
                dmax, imax = d, i
        if dmax > tol and imax is not None:
            dp(lo, imax, keep)
            keep.add(imax)
            dp(imax, hi, keep)

    keep = {0, len(pts) - 1}
    dp(0, len(pts) - 1, keep)
    return [pts[i] for i in sorted(keep)]


def detect_fields_cdl(selection, include_pasture=False, year=None):
    """Selection polygon [{lat,lon}] -> auto-traced field polygons.

    Returns {"fields": [{polygon, holes, acres, crop}], "year": used_year}.
    `polygon` follows the exact classified footprint edge; `holes` are
    interior non-crop islands (>= MIN_HOLE_PX) for use as keepouts.
    Raises ValueError on bad selection, RuntimeError when CropScape fails.
    """
    if len(selection) < 3:
        raise ValueError("selection polygon needs at least 3 vertices")

    xs, ys = zip(*(to_albers(p["lat"], p["lon"]) for p in selection))
    if (max(xs) - min(xs)) > MAX_SPAN_M or (max(ys) - min(ys)) > MAX_SPAN_M:
        raise ValueError(f"selection too large — keep it under {MAX_SPAN_M/1000:.0f} km across")
    bbox = (min(xs), min(ys), max(xs), max(ys))

    # `year` in the key: an explicit year=2023 request must never be served a
    # cached 2025 result for the same bbox (or vice versa).
    key = (round(bbox[0], -1), round(bbox[1], -1), round(bbox[2], -1),
           round(bbox[3], -1), include_pasture, year)
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]

    codes = CROP_CODES | (PASTURE_CODES if include_pasture else set())
    years = [year] if year else [2025, 2024, 2023]
    deadline = now + DETECT_BUDGET_S
    grid = gt = None
    last_err = None
    used_year = None
    for yr in years:
        try:
            grid, gt = _fetch_clip(bbox, yr, deadline)
            used_year = yr
            break
        except Exception as e:  # noqa: BLE001 — try older year
            last_err = e
            if time.time() >= deadline:
                break
    if grid is None:
        raise RuntimeError(f"CDL fetch failed: {last_err}")

    h, w = len(grid), len(grid[0])
    mask = [[grid[r][c] in codes for c in range(w)] for r in range(h)]
    mask = _despeckle(mask, w, h, grid)

    comps = [c for c in _segment(mask, w, h) if len(c) >= MIN_FIELD_PX]
    comps.sort(key=len, reverse=True)

    ulx, uly, sx, sy = gt

    def corners_to_geo(loop_pts):
        poly = []
        for cx, cy in loop_pts:
            x = ulx + cx * sx
            y = uly - cy * sy
            lat, lon = from_albers(x, y)
            poly.append({"lat": round(lat, 7), "lon": round(lon, 7)})
        return poly

    fields = []
    for comp in comps[:MAX_FIELDS]:
        loops = _edge_loops(comp)
        if not loops:
            continue
        loops.sort(key=_loop_area_px, reverse=True)
        outer = _simplify(_collapse_collinear(loops[0]), SIMPLIFY_TOL_PX)
        if len(outer) < 3:
            continue
        holes = []
        for hl in loops[1:]:
            if _loop_area_px(hl) < MIN_HOLE_PX:
                continue
            # Holes become NO-SPRAY keepouts, so they are never
            # Douglas-Peucker simplified: DP moves the traced boundary up to
            # SIMPLIFY_TOL_PX (0.9 px = 27 m) in EITHER direction, and an
            # inward move re-labels real pond/farmstead area as sprayable —
            # more than the default 15 m water buffer, i.e. spray in the
            # pond. _collapse_collinear is lossless (drops midpoints of
            # straight runs only), so the exact pixel-edge ring stays small.
            hp = _collapse_collinear(hl)
            if len(hp) >= 3:
                holes.append(corners_to_geo(hp))

        # Dominant crop for the label.
        counts = {}
        for r, c in comp:
            counts[grid[r][c]] = counts.get(grid[r][c], 0) + 1
        top = max(counts, key=counts.get)
        fields.append({
            "polygon": corners_to_geo(outer),
            "holes": holes,
            "acres": round(len(comp) * ACRES_PER_PX, 1),
            "crop": CROP_NAMES.get(top, "Cropland"),
        })

    result = {"fields": fields, "year": used_year,
              # More components existed than MAX_FIELDS: the list is the
              # largest N, not everything — callers must say so.
              "truncated": len(comps) > MAX_FIELDS}
    _cache[key] = (now, result)
    if len(_cache) > 32:
        _cache.pop(next(iter(_cache)))
    return result

"""Spray-coverage path planning for polygon fields.

Pure geometry, no vehicle or MAVLink dependencies, so it can be unit-tested
without a SITL instance and reused by any router or CLI tool.

Approach: project the field polygon onto a local equirectangular plane
(meters) centered on its centroid, rotate so the sweep direction lies along
the +x axis, slice the polygon with horizontal lines spaced one swath apart,
and stitch the resulting in-polygon segments into a boustrophedon (serpentine)
path. A local flat projection is accurate to well under a meter for fields a
few km across, which is far below GPS and spray-drift error.

No-spray keepouts (water/trees/buildings) are clipped in the same rotated
frame: every pass stays a horizontal segment there, so removing the buffered
keepout region is exact 1-D interval subtraction along x rather than general
polygon clipping.
"""

import math
from typing import Optional

# IUGG mean earth radius. Any consistent radius works for a local projection;
# the same constant must be used to project and unproject.
EARTH_RADIUS_M = 6371008.8

_M2_PER_ACRE = 4046.8564224


def plan_coverage(
    polygon: list[dict],
    swath_m: float,
    alt_m: float,
    angle_deg: Optional[float] = None,
    speed_ms: float = 18.0,
    keepouts: Optional[list[list[dict]]] = None,
    keepout_buffer_m: float = 0.0,
) -> dict:
    """Plan a serpentine spray pattern over a lat/lon polygon.

    Args:
        polygon: field boundary as [{"lat": ..., "lon": ...}, ...] (>= 3 vertices).
        swath_m: spray swath width in meters (> 0); also the pass spacing.
        alt_m: AGL altitude assigned to every waypoint.
        angle_deg: sweep direction in degrees CCW from local east. When None,
            the orientation of the polygon's longest edge is used — long passes
            with few turns are almost always what an operator wants.
        speed_ms: cruise speed used only for the time estimate.
        keepouts: optional no-spray polygons (water/trees/buildings), each a
            list of {"lat", "lon"} dicts with >= 3 vertices. Spray passes are
            clipped so NO point of any sprayed segment lies within
            keepout_buffer_m of any keepout (inside a keepout counts as
            distance 0). A pass crossing a keepout splits into sub-segments
            outside the buffered zone; sub-segments shorter than
            _MIN_SEGMENT_M are dropped — too short to cycle the spray valve.
            LIMITATION: transit hops between passes are NOT rerouted around
            keepouts in this version. The drone may overfly a keepout between
            passes with the sprayer off; it just never sprays inside the
            buffered zone.
        keepout_buffer_m: extra standoff in meters around every keepout (>= 0).

    Returns:
        {"waypoints": [{"lat", "lon", "alt"}, ...],  # segment endpoints in flight order
         "stats": {area_m2, area_acres, n_passes, path_length_m, est_time_s,
                   swath_m, angle_deg}}
        When keepouts is not None, stats additionally carries
        "keepouts_applied" (how many keepout polygons actually removed spray
        length from at least one pass) and "n_segments" (spray sub-segments
        after clipping; n_passes stays the pre-clip sweep-pass count). Calls
        without keepouts keep the exact legacy shape so existing clients see
        byte-for-byte identical responses.

    Raises:
        ValueError: fewer than 3 vertices, zero-area (degenerate) polygon,
            non-positive swath/speed, a malformed keepout (< 3 vertices or
            non-{lat, lon} entries), a negative buffer, when clipping
            removes every spray segment ("field fully blocked by keepout
            zones"), or when the passes x keepout-edges clipping work would
            exceed _MAX_CLIP_WORK (CPU-DoS guard: giant field + tiny swath
            + dense keepouts).
    """
    if len(polygon) < 3:
        raise ValueError("polygon needs at least 3 vertices")
    if swath_m <= 0:
        raise ValueError("swath_m must be > 0")
    # A plain `<= 0` check lets NaN through (NaN <= 0 is False), which would
    # poison est_time_s; +/-inf gives a silently bogus estimate. Require a
    # finite positive speed, same pattern as keepout_buffer_m below.
    if not (math.isfinite(speed_ms) and speed_ms > 0):
        raise ValueError("speed_ms must be a finite value > 0")
    # NaN would silently disable every comparison below, so reject it too.
    if not (math.isfinite(keepout_buffer_m) and keepout_buffer_m >= 0.0):
        raise ValueError("keepout_buffer_m must be >= 0")
    if keepouts is not None:
        for i, kp in enumerate(keepouts):
            if not isinstance(kp, (list, tuple)) or len(kp) < 3:
                raise ValueError(f"keepout {i} needs at least 3 vertices")
            for p in kp:
                if not isinstance(p, dict) or "lat" not in p or "lon" not in p:
                    raise ValueError(
                        f"keepout {i} vertices must be {{lat, lon}} dicts")

    pts, proj = _project(polygon)
    area_m2 = _shoelace_area(pts)
    if area_m2 < 1.0:
        # Collinear or repeated vertices enclose nothing sprayable; reject
        # rather than return an empty "successful" plan. 1 m^2 is far below
        # any real field yet comfortably above shoelace float noise.
        raise ValueError("polygon has zero area")

    theta = angle_deg if angle_deg is not None else _longest_edge_angle(pts)
    theta %= 180.0  # a sweep line has no preferred direction

    # Rotate by -theta so sweep lines become horizontal (y = const).
    cos_t = math.cos(math.radians(theta))
    sin_t = math.sin(math.radians(theta))
    rot = [(x * cos_t + y * sin_t, -x * sin_t + y * cos_t) for x, y in pts]

    passes = _boustrophedon_passes(rot, swath_m)

    if keepouts is None:
        # Legacy path: no clipping, no extra stats keys, identical output.
        segments = passes
        keepouts_applied = 0
    else:
        segments, keepouts_applied = _clip_passes_to_keepouts(
            passes, keepouts, keepout_buffer_m, proj, cos_t, sin_t)
        if not segments:
            raise ValueError("field fully blocked by keepout zones")

    # Walk the full polyline (rotation preserves distance, so measure here):
    # segment lengths plus the hop from each segment end to the next start.
    # Hops are straight lines and are NOT rerouted around keepouts — the
    # sprayer is off during transit, so overflight is acceptable there.
    path_length = 0.0
    flat: list[tuple[float, float]] = []
    for a, b in segments:
        flat.extend((a, b))
    for i in range(1, len(flat)):
        path_length += math.dist(flat[i - 1], flat[i])

    # Undo the rotation, then unproject back to lat/lon.
    waypoints = []
    for rx, ry in flat:
        x = rx * cos_t - ry * sin_t
        y = rx * sin_t + ry * cos_t
        lat, lon = _unproject(x, y, proj)
        waypoints.append({"lat": lat, "lon": lon, "alt": alt_m})

    result = {
        "waypoints": waypoints,
        "stats": {
            "area_m2": area_m2,
            "area_acres": area_m2 / _M2_PER_ACRE,
            "n_passes": len(passes),
            "path_length_m": path_length,
            "est_time_s": path_length / speed_ms,
            "swath_m": swath_m,
            "angle_deg": theta,
        },
    }
    if keepouts is not None:
        # Only in keepout mode: pre-keepout clients keep the exact legacy
        # stats shape (regression-pinned in tests).
        result["stats"]["keepouts_applied"] = keepouts_applied
        result["stats"]["n_segments"] = len(segments)
    return result


# --- local projection -------------------------------------------------------

def _project(polygon: list[dict]) -> tuple[list[tuple[float, float]], tuple]:
    """Equirectangular projection to meters around the vertex centroid.

    The cos(lat) factor corrects for meridian convergence; frozen at the
    centroid latitude, which is plenty accurate at field scale.
    """
    lat0 = sum(p["lat"] for p in polygon) / len(polygon)
    lon0 = sum(p["lon"] for p in polygon) / len(polygon)
    m_per_deg = math.pi / 180.0 * EARTH_RADIUS_M
    cos_lat = math.cos(math.radians(lat0))
    pts = [
        ((p["lon"] - lon0) * m_per_deg * cos_lat, (p["lat"] - lat0) * m_per_deg)
        for p in polygon
    ]
    return pts, (lat0, lon0, m_per_deg, cos_lat)


def _unproject(x: float, y: float, proj: tuple) -> tuple[float, float]:
    """Inverse of _project: local meters -> (lat, lon)."""
    lat0, lon0, m_per_deg, cos_lat = proj
    return lat0 + y / m_per_deg, lon0 + x / (m_per_deg * cos_lat)


# --- geometry helpers -------------------------------------------------------

def _shoelace_area(pts: list[tuple[float, float]]) -> float:
    """Unsigned polygon area (m^2) via the shoelace formula."""
    s = 0.0
    for i, (x1, y1) in enumerate(pts):
        x2, y2 = pts[(i + 1) % len(pts)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def _longest_edge_angle(pts: list[tuple[float, float]]) -> float:
    """Orientation (deg CCW from +x, in [0, 180)) of the longest polygon edge."""
    best_len2 = -1.0
    best_angle = 0.0
    for i, (x1, y1) in enumerate(pts):
        x2, y2 = pts[(i + 1) % len(pts)]
        dx, dy = x2 - x1, y2 - y1
        len2 = dx * dx + dy * dy
        if len2 > best_len2:
            best_len2 = len2
            best_angle = math.degrees(math.atan2(dy, dx))
    return best_angle % 180.0


def _line_crossings(pts: list[tuple[float, float]], c: float) -> list[tuple[float, float]]:
    """In-polygon x-intervals where the horizontal line y=c crosses the polygon.

    Uses the half-open rule (y1 <= c < y2) so a vertex lying exactly on the
    line is counted once when the boundary crosses and zero/twice when it only
    touches — crossing parity stays correct, and a concave polygon naturally
    yields multiple disjoint intervals.
    """
    xs = []
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if (y1 <= c < y2) or (y2 <= c < y1):
            t = (c - y1) / (y2 - y1)
            xs.append(x1 + t * (x2 - x1))
    xs.sort()
    segments = []
    for j in range(0, len(xs) - 1, 2):
        if xs[j + 1] - xs[j] > 1e-9:  # drop tangential zero-length touches
            segments.append((xs[j], xs[j + 1]))
    return segments


def _boustrophedon_passes(
    rot: list[tuple[float, float]], swath_m: float
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Ordered spray passes ((start, end) in rotated coords) for the polygon.

    Sweep lines are horizontal, spaced swath_m apart. Each pass sprays a
    swath_m-wide band centered on its line, so ceil(height / swath_m) lines
    always blanket the rotated bounding box; the grid is centered in the
    extent so the overhang past each edge never exceeds swath_m / 2 and every
    line lies strictly inside (y_min, y_max) — a line exactly on the boundary
    would be dropped by the half-open crossing rule. Passes are stitched
    greedily: from the end of the previous pass, always enter the nearest
    remaining segment endpoint on the current line. For single-segment lines
    this reduces to the classic serpentine alternation; for concave fields
    (multiple segments per line) it gives a sensible nearest-neighbor tour.
    """
    ys = [y for _, y in rot]
    xs = [x for x, _ in rot]
    y_min, y_max = min(ys), max(ys)
    height = y_max - y_min

    # The epsilon keeps an extent that is an exact multiple of the swath from
    # rounding up to a needless extra line; max(1, ...) still flies one pass
    # down the middle of a field narrower than a swath.
    n_lines = max(1, math.ceil(height / swath_m - 1e-9))
    first_y = y_min + (height - (n_lines - 1) * swath_m) / 2.0
    line_ys = [first_y + k * swath_m for k in range(n_lines)]

    passes = []
    cur = (min(xs), y_min)  # nominal start corner -> first pass flies west-to-east
    for c in line_ys:
        remaining = _line_crossings(rot, c)
        while remaining:
            best = None  # (distance, seg index, reversed?)
            for idx, (xa, xb) in enumerate(remaining):
                for start_x, reverse in ((xa, False), (xb, True)):
                    d = math.dist(cur, (start_x, c))
                    if best is None or d < best[0]:
                        best = (d, idx, reverse)
            _, idx, reverse = best
            xa, xb = remaining.pop(idx)
            start, end = ((xb, c), (xa, c)) if reverse else ((xa, c), (xb, c))
            passes.append((start, end))
            cur = end
    return passes


# --- keepout clipping ---------------------------------------------------------

# Sub-segments shorter than this are dropped after keepout clipping: they are
# too short for the sprayer to cycle on/off and only add turn overhead.
_MIN_SEGMENT_M = 2.0

# Hard ceiling on clipping work, counted in keepout-edge visits actually
# performed (after the y-band prefilter). The routers cap vertex and ring
# COUNTS, but not the field's geographic extent, so n_passes — and with it
# the passes x keepout-edges product — is otherwise unbounded: one legal
# request (huge field, 0.51 m swath, max keepouts) could pin this GIL-bound
# CPU for minutes and starve the rest of the GCS backend, telemetry
# included. 2e6 visits is ~2 s of worst-case CPU, yet far above real jobs
# (a 2 km field at 3 m swath with dozens of pond-sized keepouts does ~1e5:
# the prefilter only charges a ring to the passes it can actually block).
_MAX_CLIP_WORK = 2_000_000


def _clip_passes_to_keepouts(
    passes: list[tuple[tuple[float, float], tuple[float, float]]],
    keepouts: list[list[dict]],
    buffer_m: float,
    proj: tuple,
    cos_t: float,
    sin_t: float,
) -> tuple[list[tuple[tuple[float, float], tuple[float, float]]], int]:
    """Clip horizontal spray passes against buffered keepout polygons.

    Works entirely in the rotated local frame the passes were built in: each
    keepout is projected with the field's projection and rotated with the
    field's sweep rotation, so every pass stays a horizontal segment and
    clipping reduces to exact 1-D interval subtraction along x.

    Returns (segments, keepouts_applied): sub-segments in flight order (the
    parent pass's direction is preserved, so serpentine ordering survives)
    and the count of keepout polygons that removed spray length from at
    least one pre-clip pass — an order-independent definition, so overlap
    between keepouts can't hide one behind another.

    Raises ValueError when the work performed (edge visits on rings that
    survive the y-band prefilter) exceeds _MAX_CLIP_WORK, so a single
    request can never burn more than a couple seconds of CPU here.
    """
    lat0, lon0, m_per_deg, cos_lat = proj
    kp_rot = []
    kp_ybounds = []  # per-ring (min y, max y) for the prefilter below
    for kp in keepouts:
        pts = []
        for p in kp:
            x = (p["lon"] - lon0) * m_per_deg * cos_lat
            y = (p["lat"] - lat0) * m_per_deg
            pts.append((x * cos_t + y * sin_t, -x * sin_t + y * cos_t))
        kp_rot.append(pts)
        ys = [y for _, y in pts]
        kp_ybounds.append((min(ys), max(ys)))

    applied: set[int] = set()
    segments = []
    work = 0  # keepout-edge visits performed; bounded by _MAX_CLIP_WORK
    for (sx, sy), (ex, _ey) in passes:  # sy == _ey: passes are horizontal
        lo, hi = (sx, ex) if sx <= ex else (ex, sx)
        blocked: list[tuple[float, float]] = []
        for k, ring in enumerate(kp_rot):
            # Exact skip, not a heuristic: every blocked interval (interior
            # crossing, vertex disc, or edge strip) requires the pass line to
            # lie within buffer_m of the ring's y-extent.
            y_lo, y_hi = kp_ybounds[k]
            if sy < y_lo - buffer_m or sy > y_hi + buffer_m:
                continue
            work += len(ring)
            if work > _MAX_CLIP_WORK:
                raise ValueError(
                    "keepout clipping too complex for this request: "
                    "increase the swath, shrink the field, or simplify "
                    "the keepouts")
            ivs = _merge_intervals(_blocked_intervals(ring, sy, buffer_m))
            for blo, bhi in ivs:
                if min(bhi, hi) - max(blo, lo) > 1e-9:
                    applied.add(k)
            blocked.extend(ivs)
        allowed = [
            (a, b)
            for a, b in _subtract_intervals(lo, hi, _merge_intervals(blocked))
            if b - a >= _MIN_SEGMENT_M
        ]
        if sx <= ex:
            segments.extend(((a, sy), (b, sy)) for a, b in allowed)
        else:  # pass flew right-to-left: keep flight order and direction
            segments.extend(((b, sy), (a, sy)) for a, b in reversed(allowed))
    return segments, len(applied)


def _blocked_intervals(
    ring: list[tuple[float, float]], c: float, buffer_m: float
) -> list[tuple[float, float]]:
    """x-intervals of the line y=c within buffer_m of the polygon `ring`.

    The buffered region is the Minkowski sum of the polygon with a disc of
    radius buffer_m, decomposed exactly as interior + per-edge capsules
    (a disc around each vertex plus the buffer_m-wide rectangle along each
    edge). Interior crossings alone handle buffer_m == 0 ("inside == 0
    distance"). Intervals may overlap and are unsorted; callers merge them.
    """
    intervals = list(_line_crossings(ring, c))  # interior of the keepout
    if buffer_m <= 0.0:
        return intervals
    n = len(ring)
    for i in range(n):
        ax, ay = ring[i]
        bx, by = ring[(i + 1) % n]
        # Disc around vertex a. Each vertex is the 'a' of exactly one edge,
        # so one disc per loop iteration covers every capsule end cap.
        d2 = buffer_m * buffer_m - (c - ay) * (c - ay)
        if d2 > 0.0:
            s = math.sqrt(d2)
            intervals.append((ax - s, ax + s))
        # Rectangle strip of half-width buffer_m along the edge; a simple
        # convex quad, so _line_crossings yields its (single) x-interval.
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length > 1e-9:  # zero-length edges have no strip (discs suffice)
            nx = -dy / length * buffer_m
            ny = dx / length * buffer_m
            quad = [(ax + nx, ay + ny), (bx + nx, by + ny),
                    (bx - nx, by - ny), (ax - nx, ay - ny)]
            intervals.extend(_line_crossings(quad, c))
    return intervals


def _merge_intervals(
    intervals: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Union of possibly-overlapping (lo, hi) intervals, sorted by lo."""
    ivs = sorted((lo, hi) for lo, hi in intervals if hi - lo > 1e-9)
    merged: list[list[float]] = []
    for lo, hi in ivs:
        # The epsilon fuses intervals that abut within float noise (e.g. a
        # vertex disc meeting its edge rectangle) so no sliver survives.
        if merged and lo <= merged[-1][1] + 1e-9:
            if hi > merged[-1][1]:
                merged[-1][1] = hi
        else:
            merged.append([lo, hi])
    return [(lo, hi) for lo, hi in merged]


def _subtract_intervals(
    lo: float, hi: float, blocked: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    """Sub-intervals of [lo, hi] left after removing `blocked`.

    `blocked` must be merged and sorted (see _merge_intervals).
    """
    out = []
    cur = lo
    for blo, bhi in blocked:
        if bhi <= cur:
            continue
        if blo >= hi:
            break
        if blo > cur:
            out.append((cur, blo))
        cur = bhi
        if cur >= hi:
            break
    if cur < hi:
        out.append((cur, hi))
    return out

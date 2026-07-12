"""Spray-coverage path planning for polygon fields.

Pure geometry, no vehicle or MAVLink dependencies, so it can be unit-tested
without a SITL instance and reused by any router or CLI tool.

Approach: project the field polygon onto a local equirectangular plane
(meters) centered on its centroid, rotate so the sweep direction lies along
the +x axis, slice the polygon with horizontal lines spaced one swath apart,
and stitch the resulting in-polygon segments into a boustrophedon (serpentine)
path. A local flat projection is accurate to well under a meter for fields a
few km across, which is far below GPS and spray-drift error.
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

    Returns:
        {"waypoints": [{"lat", "lon", "alt"}, ...],  # pass endpoints in flight order
         "stats": {area_m2, area_acres, n_passes, path_length_m, est_time_s,
                   swath_m, angle_deg}}

    Raises:
        ValueError: fewer than 3 vertices, zero-area (degenerate) polygon,
            or non-positive swath/speed.
    """
    if len(polygon) < 3:
        raise ValueError("polygon needs at least 3 vertices")
    if swath_m <= 0:
        raise ValueError("swath_m must be > 0")
    if speed_ms <= 0:
        raise ValueError("speed_ms must be > 0")

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

    # Walk the full polyline (rotation preserves distance, so measure here):
    # segment lengths plus the hop from each pass end to the next pass start.
    path_length = 0.0
    flat: list[tuple[float, float]] = []
    for a, b in passes:
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

    return {
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

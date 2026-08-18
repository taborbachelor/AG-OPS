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

from app.reroute import hazard_hull, hull_tolerance, route_leg

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
    work_budget: Optional[list] = None,
    hazards: Optional[list[list[dict]]] = None,
    hazard_buffer_m: float = 0.0,
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
            Connecting legs are NOT rerouted around ordinary keepouts: the
            aircraft may physically overfly a pond or tree stand between
            passes (sprayer off), which costs nothing. stats.keepout_
            overflights counts those so the UI can warn. Pass `hazards` for
            the keepouts that must actually be flown around.
        keepout_buffer_m: extra standoff in meters around every keepout (>= 0).
        hazards: optional subset of keepouts that are AIRFRAME hazards rather
            than spray-quality zones — powerlines, today. Connecting legs
            (both the hops between passes and the hop across a clipped pass)
            are rerouted around these with hazard_buffer_m of clearance,
            because overflying one is a crash rather than a wasted pass.
            Rings should ALSO appear in `keepouts` if their spray passes
            need clipping; this argument only controls leg routing.
            When a leg cannot be routed — an endpoint inside a hazard, or
            geometry too tangled — the straight leg is kept and counted in
            stats.hazard_overflights so the operator is warned rather than
            being shown a plan that silently still crosses a line.
        hazard_buffer_m: lateral flight clearance around hazards (>= 0).

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
        Also returns "leg_kinds": one entry per consecutive waypoint pair,
        each "spray" | "hop" | "detour". A detour inserts extra waypoints, so
        a caller can no longer assume waypoints arrive in strict spray pairs —
        read leg_kinds instead of inferring structure from the index parity.
        With hazards set, stats carries "hazard_reroutes" (legs routed around
        a hazard) and "hazard_overflights" (legs that STILL cross one).

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
    if not (math.isfinite(hazard_buffer_m) and hazard_buffer_m >= 0.0):
        raise ValueError("hazard_buffer_m must be >= 0")
    if hazards is not None:
        for i, hz in enumerate(hazards):
            if not isinstance(hz, (list, tuple)) or len(hz) < 3:
                raise ValueError(f"hazard {i} needs at least 3 vertices")
            for p in hz:
                if not isinstance(p, dict) or "lat" not in p or "lon" not in p:
                    raise ValueError(
                        f"hazard {i} vertices must be {{lat, lon}} dicts")
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

    # ONE CPU allowance for this plan, shared by keepout clipping AND hazard
    # rerouting. Initialised here rather than inside each phase: two separate
    # defaults would let a single request spend twice the DoS budget.
    # Slack for the hull's polygon approximation — without it, spray endpoints
    # clipped at exactly the buffer distance read as inside the hazard and no
    # leg can be routed at all.
    hazard_tol = hull_tolerance(hazard_buffer_m)

    if work_budget is None:
        work_budget = [_MAX_CLIP_WORK]

    def _charge(n: int):
        work_budget[0] -= n
        if work_budget[0] < 0:
            raise ValueError(
                "keepout clipping too complex for this request: "
                "increase the swath, shrink the field/job, or simplify "
                "the keepouts")

    if keepouts is None:
        # Legacy path: no clipping, no extra stats keys, identical output.
        segments = passes
        keepouts_applied = 0
        overflights = 0
    else:
        segments, keepouts_applied, overflights = _clip_passes_to_keepouts(
            passes, keepouts, keepout_buffer_m, proj, cos_t, sin_t,
            work_budget=work_budget)
        if not segments:
            raise ValueError("field fully blocked by keepout zones")

    # Hazard hulls live in the SAME rotated frame as the passes, so routing
    # and clipping share one coordinate system and no conversion can drift.
    hazard_hulls = []
    if hazards:
        for hz in hazards:
            ring_rot = []
            lat0, lon0, m_per_deg, cos_lat = proj
            for p in hz:
                x = (p["lon"] - lon0) * m_per_deg * cos_lat
                y = (p["lat"] - lat0) * m_per_deg
                ring_rot.append((x * cos_t + y * sin_t, -x * sin_t + y * cos_t))
            hull = hazard_hull(ring_rot, hazard_buffer_m)
            if len(hull) >= 3:
                hazard_hulls.append(hull)

    # Reorder the spray sub-segments so passes reachable WITHOUT crossing a
    # hazard are flown together. A field bisected by a power line otherwise
    # alternates sides on every pass, and rerouting each of those crossings
    # produced a mission ~2.6x longer than the straight-line plan (measured).
    # Flying one side, crossing once, then the other side gets that back.
    # Only runs when a hazard actually blocks a leg, so hazard-free plans keep
    # the exact serpentine ordering they have always had.
    if hazard_hulls and len(segments) > 2:
        segments = _order_segments_around_hazards(
            segments, hazard_hulls, hazard_tol, _charge)

    # Walk the full polyline (rotation preserves distance, so measure here).
    # Spray sub-segments are joined by connecting legs; a leg that would cross
    # a HAZARD is rerouted around it, and the detour points become real
    # waypoints. leg_kinds records what each consecutive pair is, because with
    # detours inserted the waypoint list is no longer strict spray pairs.
    flat: list[tuple[float, float]] = []
    leg_kinds: list[str] = []
    hazard_reroutes = 0
    hazard_overflights = 0

    def _add(pt, kind):
        if flat:
            leg_kinds.append(kind)
        flat.append(pt)

    for si, (a, b) in enumerate(segments):
        if si == 0:
            _add(a, None)
        elif not hazard_hulls:
            _add(a, "hop")
        else:
            detour = route_leg(flat[-1], a, hazard_hulls, charge=_charge,
                               tol_m=hazard_tol)
            if detour is None:
                # Unroutable: keep the straight leg, but NEVER silently — the
                # operator has to know this one still crosses a hazard.
                hazard_overflights += 1
                _add(a, "hop")
            elif detour:
                hazard_reroutes += 1
                for d in detour:
                    _add(d, "detour")
                _add(a, "detour")
            else:
                _add(a, "hop")
        _add(b, "spray")

    path_length = 0.0
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
        "leg_kinds": leg_kinds,
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
        # Connecting legs that physically cross a keepout polygon (the
        # aircraft overflies it; sprayer must be off) — surfaced so the UI
        # can warn instead of the plan looking fully "avoided".
        result["stats"]["keepout_overflights"] = overflights
    if hazards is not None:
        result["stats"]["hazard_reroutes"] = hazard_reroutes
        # The number that matters: legs that STILL cross a hazard because
        # routing could not resolve them. Must reach the operator.
        result["stats"]["hazard_overflights"] = hazard_overflights
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
    work_budget: Optional[list] = None,
) -> tuple[list[tuple[tuple[float, float], tuple[float, float]]], int, int]:
    """Clip horizontal spray passes against buffered keepout polygons.

    Works entirely in the rotated local frame the passes were built in: each
    keepout is projected with the field's projection and rotated with the
    field's sweep rotation, so every pass stays a horizontal segment and
    clipping reduces to exact 1-D interval subtraction along x.

    Returns (segments, keepouts_applied, overflights): sub-segments in flight
    order (the parent pass's direction is preserved, so serpentine ordering
    survives), the count of keepout polygons that removed spray length from
    at least one pre-clip pass (order-independent, so overlap between
    keepouts can't hide one behind another), and the number of connecting
    legs between consecutive sub-segments that physically cross a keepout
    polygon.

    Raises ValueError when the work performed (edge visits on rings that
    survive the y-band prefilter) exceeds the work budget — per call by
    default, shared across a whole job when the caller passes work_budget —
    so a single request can never burn more than a couple seconds of CPU.
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

    if work_budget is None:
        work_budget = [_MAX_CLIP_WORK]

    def _charge(n: int):
        work_budget[0] -= n
        if work_budget[0] < 0:
            raise ValueError(
                "keepout clipping too complex for this request: "
                "increase the swath, shrink the field/job, or simplify "
                "the keepouts")

    applied: set[int] = set()
    segments = []
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
            _charge(len(ring))
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

    # Count connecting legs (end of one sub-segment -> start of the next)
    # that cross a keepout ring: the aircraft overflies the zone there.
    overflights = 0
    for i in range(1, len(segments)):
        a = segments[i - 1][1]
        b = segments[i][0]
        if a == b:
            continue
        for k, ring in enumerate(kp_rot):
            y_lo, y_hi = kp_ybounds[k]
            if max(a[1], b[1]) < y_lo or min(a[1], b[1]) > y_hi:
                continue
            _charge(len(ring))
            if _segment_crosses_ring(a, b, ring):
                overflights += 1
                break
    return segments, len(applied), overflights


def _order_segments_around_hazards(segments, hulls, tol, charge):
    """Greedy tour over spray sub-segments that avoids hazard crossings.

    From the current position, prefer the nearest unflown sub-segment whose
    connecting leg is hazard-free, entering it from whichever end is closer;
    only when NO reachable sub-segment is hazard-free do we accept a crossing
    (which the caller then reroutes). On a field split by one line that means
    a single crossing instead of one per pass.

    Returns the segments reordered (and individually reversed where entering
    from the far end is closer). Direction within a pass does not matter for
    spray coverage, which is what makes the reversal free.

    The first segment is kept as the start so the pattern still begins where
    the serpentine did.
    """
    def blocked(a, b):
        for hull in hulls:
            charge(len(hull))
            if _segment_enters(a, b, hull, tol):
                return True
        return False

    remaining = list(segments[1:])
    ordered = [segments[0]]
    pos = segments[0][1]
    while remaining:
        # Rank every candidate entry by distance FIRST, then walk that order
        # and stop at the first hazard-free one. Testing every candidate
        # instead burned ~26% of the shared job CPU budget on a single
        # 200-segment field, which would fail a 4-field job closed; the
        # nearest candidate is almost always clear, so this is normally one
        # blocked() test per step rather than 2n of them. Same choice either
        # way — nearest unflown segment whose leg is clear.
        cands = sorted(
            ((math.dist(pos, entry), i, rev)
             for i, seg in enumerate(remaining)
             for rev, entry in ((False, seg[0]), (True, seg[1]))),
            key=lambda c: c[0])
        pick = None
        for d, i, rev in cands:
            if not blocked(pos, (remaining[i][1] if rev else remaining[i][0])):
                pick = (d, i, rev)
                break
        if pick is None:
            pick = cands[0]     # boxed in: accept a crossing, caller reroutes
        _, idx, rev = pick
        seg = remaining.pop(idx)
        if rev:
            seg = (seg[1], seg[0])
        ordered.append(seg)
        pos = seg[1]
    return ordered


def _segment_enters(a, b, hull, tol):
    """Thin wrapper so the ordering helper reads clearly."""
    from app.reroute import segment_enters_hull
    return segment_enters_hull(a, b, hull, eps_m=max(tol, 1e-9))


def _seg_intersect(p, q, r, s) -> bool:
    """True if segments pq and rs properly intersect (orientation test)."""
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    d1, d2 = cross(r, s, p), cross(r, s, q)
    d3, d4 = cross(p, q, r), cross(p, q, s)
    return (((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))
            and d1 != d2 and d3 != d4)


def _point_in_ring_xy(x: float, y: float,
                      ring: list[tuple[float, float]]) -> bool:
    """Ray-cast point-in-polygon on (x, y) tuples."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y):
            if x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside


def _segment_crosses_ring(a, b, ring) -> bool:
    """True if segment ab enters keepout `ring`: crosses its boundary or has
    its midpoint inside (covers a leg fully contained in the polygon)."""
    n = len(ring)
    for i in range(n):
        if _seg_intersect(a, b, ring[i], ring[(i + 1) % n]):
            return True
    mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
    return _point_in_ring_xy(mx, my, ring)


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

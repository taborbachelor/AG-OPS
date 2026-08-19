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

_G_MS2 = 9.80665

# Planner-side commanded-bank ceiling, in degrees. NOT the same number as
# guardian's bank_warn_deg (45 deg = ArduPlane's ROLL_LIMIT_DEG default), and
# deliberately well under guardian's LOW-ALTITUDE threshold of 31.5 deg
# (45 * bank_low_alt_factor 0.7, applied below 30 m -- i.e. the whole of a spray
# pass). The gap between 25 and 31.5 is the margin the aircraft needs to not
# trip its own monitor on every turn: L1 overshoot, wind gradient and gusts all
# add bank on top of what the geometry commands. Planning to the monitor's
# threshold would guarantee a warning on every headland.
# See LANES.md seam S2 -- AIR owns the final agreement between these two numbers.
DEFAULT_MAX_BANK_DEG = 25.0

# Full rationale, physics and limits: the turn-geometry section further down.


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
    max_bank_deg: float = DEFAULT_MAX_BANK_DEG,
    headlands: bool = True,
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
        max_bank_deg: ceiling on the bank the plan is allowed to COMMAND in a
            turn (0 disables the constraint and restores the plain
            adjacent-line serpentine). Passes are ordered so each direction
            reversal has 2 * R_min of lateral room at speed_ms; see the
            turn-geometry section. The achieved geometry is always reported in
            stats.turn_* -- on a field too narrow to satisfy the limit the
            planner flies the widest turns available and says so, rather than
            failing a plan the operator needs.
        headlands: widen each pass to cover the full swath-deep band it sprays
            rather than only the line through its middle, closing the sawtooth
            strip along a slanted or traced boundary. Costs up to half a swath
            of overspray past the boundary where the edge slants away -- the
            same overhang the sweep grid already produces in the other axis.
            False restores line-exact extents. See the headlands section.

    Returns:
        {"waypoints": [{"lat", "lon", "alt"}, ...],  # segment endpoints in flight order
         "stats": {area_m2, area_acres, n_passes, path_length_m, est_time_s,
                   swath_m, angle_deg}}
        When keepouts is not None, stats additionally carries
        "keepouts_applied" (how many keepout polygons actually removed spray
        length from at least one pass) and "n_segments" (spray sub-segments
        after clipping; n_passes stays the pre-clip sweep-pass count), plus
        coverage analysis — "coverage_pct", "sprayable_acres",
        "uncovered_acres" — measuring how much of the ground we INTENDED to
        spray the passes actually hit (keepout area is excluded from the
        denominator, since not spraying a pond is the plan working, not a
        gap). Calls without keepouts keep the exact legacy stats shape so
        existing clients see identical responses; pass keepouts=[] to opt
        into the extra stats on a field with none.
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
    # NaN would disable the comparison and silently drop the constraint; 90 deg
    # is a vertical bank (infinite load factor), not a limit.
    if not (math.isfinite(max_bank_deg) and 0.0 <= max_bank_deg < 90.0):
        raise ValueError("max_bank_deg must be a finite value in [0, 90)")
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

    # Lateral room every reversal needs: reversing between two passes d apart
    # is a half-circle of radius d / 2, so d = 2R at the bank limit.
    min_turn_spacing_m = (0.0 if max_bank_deg <= 0.0
                          else 2.0 * turn_radius_m(speed_ms, max_bank_deg))
    # Built here rather than earlier so headland widening charges the SAME CPU
    # budget as keepout clipping and hazard routing -- one allowance per plan.
    headland_stats: dict = {}
    passes = _boustrophedon_passes(rot, swath_m, min_turn_spacing_m,
                                   headlands=headlands, charge=_charge,
                                   stats_out=headland_stats)

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
            segments, hazard_hulls, hazard_tol, _charge, min_turn_spacing_m)

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
            straight = math.dist(flat[-1], a)
            detour = route_leg(flat[-1], a, hazard_hulls, charge=_charge,
                               tol_m=hazard_tol,
                               max_extra_m=_detour_budget_m(straight))
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
    # Turn geometry, measured on the ordered spray sequence that is actually
    # flown (segments, not the pre-clip passes): the bank this plan commands,
    # whether it met the limit, and the speed that would if it did not.
    result["stats"].update(_turn_stats(segments, speed_ms, max_bank_deg))
    # How much sprayed length the headland widening added, and to how many
    # passes. Zero on a field whose edges run parallel to the passes -- there is
    # no sawtooth to close there, and saying so is more useful than silence.
    result["stats"].update(headland_stats)
    if keepouts is not None:
        # Only in keepout mode: pre-keepout clients keep the exact legacy
        # stats shape (regression-pinned in tests).
        result["stats"]["keepouts_applied"] = keepouts_applied
        result["stats"]["n_segments"] = len(segments)
        # Connecting legs that physically cross a keepout polygon (the
        # aircraft overflies it; sprayer must be off) — surfaced so the UI
        # can warn instead of the plan looking fully "avoided".
        result["stats"]["keepout_overflights"] = overflights
    # Coverage analysis: what fraction of the sprayable ground the passes
    # actually hit. Cheap in the rotated frame and it answers the question an
    # operator actually has ("did we cover the field?"), which pass counts and
    # path length do not.
    if keepouts is not None:
        # Gated exactly like keepouts_applied/n_segments above. A call without
        # keepouts is contractually indistinguishable from the pre-keepout
        # planner (TestLegacyRegression pins the exact stats key set), and
        # that promise is not mine to break for a diagnostic. Every product
        # path — plan_auto, plan_multi — passes keepouts, so they all get it;
        # a bare /plan caller opts in by passing keepouts=[].
        try:
            kp_rot_cov, kp_bounds_cov = _project_rings(
                keepouts, proj, cos_t, sin_t)
            result["stats"].update(_coverage_stats(
                rot, segments, kp_rot_cov, kp_bounds_cov,
                keepout_buffer_m, swath_m, _charge))
        except ValueError:
            # Budget exhaustion must not lose an otherwise good plan —
            # coverage is a diagnostic, not part of the flight path.
            pass
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


# --- headlands ----------------------------------------------------------------
#
# WHY THIS EXISTS. Coverage analysis put 0.41 and 0.56 acres of genuinely missed
# ground on real 40-acre Sabetha fields. Measured again 2026-08-19 on a field
# with a traced (rather than hand-drawn) boundary: 0.93 acres missed, of which
# **0.76 was along the field boundary and only 0.17 around the keepout** -- so
# this is a boundary problem first and a keepout problem a distant second.
#
# THE MECHANISM. A pass sprays a swath_m-wide BAND centred on its line, but its
# sprayed extent is clipped to where the field boundary sits AT THE LINE. Any
# row inside the band where the field reaches further out is missed. On a
# straight edge parallel to the passes that is nothing; on a slanted or traced
# edge it is a sawtooth strip half a swath deep running the whole boundary --
# which is exactly the headland a ground rig would close with a perimeter lap.
#
# WHY NOT A PERIMETER LAP. That is the ground-rig answer and it is wrong for an
# aircraft. A boundary lap is a closed ring whose corners are the field's own
# corners: 90-degree turns at a point, which at spray speed demand a bank no
# airframe can fly (see the turn-geometry section -- we just spent +38% path
# length getting rid of exactly that). It would also add a whole extra lap of
# flight time. Instead each pass is EXTENDED to cover its own band: compute the
# field crossings over the full [c - swath/2, c + swath/2] strip rather than
# only at y = c, and stretch the pass to the widest the field gets in there.
# No new passes, no new turns, no change to the turn geometry, and the extra
# distance is a few metres per pass end.
#
# THE EXTREMES ARE EXACT, not sampled. Between vertices a polygon edge is
# straight, so x(y) is linear and the widest crossing over a band can only occur
# at a band edge or at a vertex inside the band. Sampling exactly those rows is
# exact for any polygon, at a couple of crossing computations per pass.
#
# WHAT IT COSTS: spray reaches up to half a swath past the boundary where the
# edge slants away. That is the same overspray the planner already accepts in
# the other axis -- the sweep grid is centred in the field extent, so the outer
# bands already overhang by up to swath/2. This makes the two axes consistent
# rather than introducing something new. Callers who cannot accept it pass
# headlands=False.
#
# KEEPOUTS ARE DELIBERATELY NOT WIDENED. The same band logic would say a pass
# should stop SHORT of where the keepout clip puts it, and the remaining ~0.17
# acres sits between the buffered keepout and the last sprayed point. Closing
# that means spraying nearer a pond than the clip allows, and the buffer is a
# chemical-drift guarantee with tests that assert it. Coverage is not worth
# eroding a standoff, so the keepout share of the miss stays missed and stays
# reported in coverage_pct.

# Rows are sampled a hair inside the band edge: the crossing rule is half-open
# in y, so a vertex sitting exactly on a sampled row is a degenerate case worth
# stepping around rather than reasoning about.
_BAND_EPS = 1e-6


def _headland_crossings(rot, c, half, vertex_ys, charge=None):
    """x-intervals for the pass on line y=c, widened to cover its whole band.

    ONLY THE OUTER ENDS MOVE. A widened interval may reach past the field's
    outer boundary -- that is the whole point, and the overspray is bounded by
    how far the edge slants within half a swath. It may NOT grow inward toward
    another interval on the same line. Those two intervals are the prongs of a
    concave field, and the gap between them is a notch the boundary genuinely
    excludes: a farmstead, a neighbour's ground, a road. The band edge below a
    U-shaped notch is solid field, so an unconfined widening reads that as
    permission to fly straight across the notch, spraying it. Caught by
    test_u_shape_splits_lines_into_two_segments, which is why every interval
    except the first keeps its left bound and every interval except the last
    keeps its right bound.
    """
    base = _line_crossings(rot, c)
    if not base or half <= 0.0:
        return base
    rows = [c - half + _BAND_EPS, c + half - _BAND_EPS]
    rows.extend(vy for vy in vertex_ys if c - half < vy < c + half)
    out = [[a, b] for a, b in base]
    for r in rows:
        if charge is not None:
            charge(len(rot))
        for a, b in _line_crossings(rot, r):
            for iv in out:
                if b >= iv[0] and a <= iv[1]:      # overlaps this pass interval
                    iv[0] = min(iv[0], a)
                    iv[1] = max(iv[1], b)
    # Confine every interior end to where it started: outward past the field
    # boundary is intended, inward across a notch is not.
    last = len(out) - 1
    for k, iv in enumerate(out):
        if k > 0:
            iv[0] = max(iv[0], base[k][0])
        if k < last:
            iv[1] = min(iv[1], base[k][1])
    return [(lo, hi) for lo, hi in out if hi - lo > 1e-9]


# --- turn geometry ------------------------------------------------------------
#
# WHY THIS EXISTS. Measured in SITL 2026-08-18: this airframe rolls to 50-65 deg
# in ordinary autopilot turns, past ArduPlane's own ROLL_LIMIT_DEG, while a real
# spray pass flies at 10-25 m AGL. A 60 deg bank raises stall speed ~41% and a
# stall-spin entered there has no recovery altitude. guardian.py's bank monitor
# already SEES this, and says so in its own comments -- but detection cannot
# make a turn gentler. This is the half that can: the planner stops commanding
# the geometry that forces the bank.
#
# THE PHYSICS. A coordinated level turn at speed V and bank phi has radius
# R = V^2 / (g * tan phi). Reversing direction between two parallel passes
# separated by d needs a half-circle of radius d/2, so a serpentine that turns
# onto the ADJACENT pass line demands R = swath/2 -- 10 m on a 20 m swath. At
# 18 m/s that is a physically impossible 73 deg of bank, which is exactly why
# the autopilot saturates its roll limit on every headland turn today. The
# aircraft cannot fly a tighter circle than physics allows, so it overshoots the
# line and banks as hard as it is permitted to.
#
# THE FIX is the one crop-dusters have used for decades: do not turn onto the
# neighbouring pass. Fly every Nth line and fill in the gaps on later sweeps, so
# each turn has N * swath of lateral room instead of one swath. Every line is
# still flown exactly once -- coverage is identical, only the ORDER changes --
# and the price is longer connecting hops (~+20% path length on a 20-acre field
# at the default limit).
#
# WHAT THIS DOES NOT COVER, deliberately, and what the reported numbers are for:
#   * A field narrower than 2 * R_min cannot satisfy the limit by ordering
#     alone -- the widest available turn is bounded by the field itself. The
#     planner then flies the widest geometry there is and REPORTS the bank it
#     still commands, rather than claiming a limit it did not meet.
#   * Speed is the other lever, and the planner does not command it (speed_ms is
#     an estimate input today). R falls with V^2, so slowing down buys far more
#     than reordering: stats.turn_max_speed_ms is the speed at which the planned
#     geometry WOULD meet the limit -- the actionable number on a narrow field.
#   * Detour corners around hazard hulls (reroute.py) are turns too, and are not
#     constrained here. They are rare and off the spray line; the serpentine
#     turnaround is the one that happens hundreds of times per job at 15 m AGL.

# Above this many sweep lines, skip the ordering search and take the direct
# construction. The search is O(n^2) worst case and n is unbounded before
# clipping (huge field / tiny swath), so it gets a ceiling like every other
# unbounded loop in this module.
_MAX_ORDER_SEARCH_LINES = 400


def turn_radius_m(speed_ms: float, bank_deg: float) -> float:
    """Radius of a coordinated level turn: R = V^2 / (g tan phi)."""
    if bank_deg <= 0.0 or bank_deg >= 90.0:
        return math.inf
    return speed_ms * speed_ms / (_G_MS2 * math.tan(math.radians(bank_deg)))


def bank_for_radius_deg(speed_ms: float, radius_m: float) -> float:
    """Bank a coordinated level turn of this radius demands at this speed."""
    if radius_m <= 0.0:
        return 90.0
    return math.degrees(math.atan(speed_ms * speed_ms / (_G_MS2 * radius_m)))


def speed_for_turn_ms(radius_m: float, bank_deg: float) -> float:
    """Fastest speed at which a turn of this radius stays inside this bank."""
    if radius_m <= 0.0 or bank_deg <= 0.0:
        return 0.0
    return math.sqrt(_G_MS2 * radius_m * math.tan(math.radians(bank_deg)))


def _stride_order(n: int, k: int) -> list[int]:
    """Every kth line, then the fill-in sweeps: 0, k, 2k, ..., 1, 1+k, ...

    Groups are NOT alternated (boustrophedon-style) between sweeps. Reversing
    every other group would shorten the return hop, but it lands the sweep
    change on two ADJACENT lines -- reintroducing the exact tight turn this
    ordering exists to remove, once per sweep.
    """
    out: list[int] = []
    for g in range(k):
        out.extend(range(g, n, k))
    return out


def _interleave_order(n: int, m: int) -> list[int]:
    """Interleave the bottom and top blocks, both walked downward.

    At m = n // 2 this reaches the widest turn ANY ordering of n lines can
    achieve (n=10: 4,9,3,8,2,7,1,6,0,5 -- every step 5 or 6 lines wide). It is
    the ordering that matters on a field too narrow for a plain stride.
    """
    lo = list(range(0, n - m))[::-1]
    hi = list(range(n - m, n))[::-1]
    out: list[int] = []
    for i in range(max(len(lo), len(hi))):
        if i < len(lo):
            out.append(lo[i])
        if i < len(hi):
            out.append(hi[i])
    return out


def _min_line_gap(order: list[int]) -> int:
    if len(order) < 2:
        return 0
    return min(abs(order[i + 1] - order[i]) for i in range(len(order) - 1))


def _order_lines(n_lines: int, target_gap: int) -> list[int]:
    """Visit order for sweep lines, putting >= target_gap lines between turns.

    Returns the cheapest ordering that MEETS the target (hop cost rises with the
    gap, so the smallest sufficient stride wins), or -- when the field is too
    narrow for any ordering to reach it -- the one that gets widest. The caller
    measures the result rather than trusting it: the achieved geometry is what
    gets reported, never the requested one.
    """
    if n_lines < 3 or target_gap <= 1:
        return list(range(n_lines))
    # No ordering of n lines can beat n // 2: lines n//2-1 and n//2 each have
    # only one partner that far away, so both would have to be path endpoints.
    target = min(target_gap, n_lines // 2)
    if n_lines > _MAX_ORDER_SEARCH_LINES:
        cand = _stride_order(n_lines, target)
        return cand if _min_line_gap(cand) >= target else list(range(n_lines))
    best: Optional[list[int]] = None
    best_gap = -1
    for t in range(target, 0, -1):
        for cand in (_stride_order(n_lines, t), _interleave_order(n_lines, t)):
            gap = _min_line_gap(cand)
            if gap > best_gap:
                best, best_gap = cand, gap
            if gap >= target:
                return cand
    return best if best is not None else list(range(n_lines))


def _turn_stats(passes, speed_ms: float, max_bank_deg: float) -> dict:
    """Measured turn geometry of a planned pass sequence.

    Measures the plan that was actually built instead of asserting the ordering
    worked -- the ordering is a request, the geometry is the fact. Only
    REVERSALS constrain: a transition that keeps the same heading is a
    reposition the autopilot flies straight, not a turn.
    """
    worst_radius = math.inf
    reversals = 0
    hammerheads = 0          # reversal with no lateral room at all (see below)
    for (a0, a1), (b0, b1) in zip(passes, passes[1:]):
        # Passes are horizontal in the rotated frame, so heading is +/-x and the
        # lateral room available to the turn is the y separation.
        if (a1[0] - a0[0]) * (b1[0] - b0[0]) >= 0:
            continue                      # same direction: no reversal to fly
        reversals += 1
        offset = abs(b0[1] - a1[1])
        if offset <= 0.0:
            # Two segments on the SAME line flown in opposite directions -- a
            # zero-radius reversal, reachable only on a concave field where one
            # sweep line splits into several segments. No ordering fixes it, so
            # count it and let it show rather than reporting an infinite bank.
            hammerheads += 1
            continue
        worst_radius = min(worst_radius, offset / 2.0)
    stats = {
        "turn_reversals": reversals,
        "turn_zero_offset": hammerheads,
        "turn_bank_limit_deg": round(max_bank_deg, 2),
    }
    if not math.isfinite(worst_radius) and hammerheads:
        worst_radius = 0.0
    if math.isfinite(worst_radius) and worst_radius > 0.0:
        bank = bank_for_radius_deg(speed_ms, worst_radius)
        stats["turn_radius_m"] = round(worst_radius, 2)
        stats["turn_bank_deg"] = round(bank, 1)
        stats["turn_bank_ok"] = bool(bank <= max_bank_deg + 1e-9)
        stats["turn_max_speed_ms"] = round(
            speed_for_turn_ms(worst_radius, max_bank_deg), 1)
    elif worst_radius == 0.0:
        stats["turn_radius_m"] = 0.0
        stats["turn_bank_deg"] = 90.0
        stats["turn_bank_ok"] = False
        stats["turn_max_speed_ms"] = 0.0
    else:
        # No reversal anywhere: a single pass, or every transition same-heading.
        stats["turn_radius_m"] = None
        stats["turn_bank_deg"] = None
        stats["turn_bank_ok"] = True
        stats["turn_max_speed_ms"] = None
    return stats


def _boustrophedon_passes(
    rot: list[tuple[float, float]], swath_m: float,
    min_turn_spacing_m: float = 0.0, headlands: bool = False,
    charge=None, stats_out: Optional[dict] = None,
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

    min_turn_spacing_m is the lateral room every direction reversal needs, from
    the caller's bank limit (see the turn-geometry section above). Lines are
    then visited in an order that provides it -- every Nth line, gaps filled on
    later sweeps -- instead of in index order. Which lines exist, and where they
    lie, is unchanged: coverage is identical and only the ORDER differs. 0 keeps
    the plain adjacent-line serpentine.

    headlands widens each pass to cover its own swath-deep band instead of just
    the line through its middle, closing the sawtooth strip along a slanted or
    traced boundary (see the headlands section). stats_out, when given, receives
    headland_passes and headland_extra_m -- how many passes were widened and by
    how much sprayed length, so the fix can be shown to have done something
    rather than assumed to.
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

    # Index order puts exactly one swath of lateral room in every reversal,
    # which is what forces the measured 50-65 deg banks. Reorder so each turn
    # gets the room the bank limit needs.
    if min_turn_spacing_m > 0.0 and n_lines > 2:
        target_gap = math.ceil(min_turn_spacing_m / swath_m - 1e-9)
        line_ys = [line_ys[i] for i in _order_lines(n_lines, target_gap)]

    # Vertex rows are where a boundary's x(y) changes slope, so they are the
    # only interior rows a band's widest crossing can occur at (see headlands).
    vertex_ys = sorted({y for _, y in rot}) if headlands else []
    half = swath_m / 2.0
    extra_m = 0.0
    widened = 0

    passes = []
    cur = (min(xs), y_min)  # nominal start corner -> first pass flies west-to-east
    for c in line_ys:
        remaining = _line_crossings(rot, c)
        if headlands:
            base_len = sum(hi - lo for lo, hi in remaining)
            remaining = _headland_crossings(rot, c, half, vertex_ys, charge)
            grew = sum(hi - lo for lo, hi in remaining) - base_len
            if grew > 1e-6:
                widened += 1
                extra_m += grew
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
    if stats_out is not None:
        stats_out["headland_passes"] = widened
        stats_out["headland_extra_m"] = round(extra_m, 1)
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
    kp_rot, kp_ybounds = _project_rings(keepouts, proj, cos_t, sin_t)

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


def _detour_budget_m(straight_len_m: float) -> float:
    """How much extra distance a hazard detour may add before we give up.

    Power lines are unbounded linear features, so "route around it" can mean
    flying kilometres to the end of the line. Cap the detour at something an
    operator would actually accept; past that, report the crossing instead of
    planning a path nobody will fly. Generous in absolute terms so a short hop
    can still take a real detour around a compact obstacle.
    """
    return max(_MIN_DETOUR_BUDGET_M, _DETOUR_RATIO * straight_len_m)


_MIN_DETOUR_BUDGET_M = 250.0
_DETOUR_RATIO = 3.0


def _project_rings(rings, proj, cos_t, sin_t):
    """Project lat/lon rings into the field's rotated local frame.

    Returns (rings_xy, y_bounds). Shared by keepout clipping and the coverage
    analysis so both see byte-identical geometry — computing it twice invites
    the two disagreeing about where a keepout is.
    """
    lat0, lon0, m_per_deg, cos_lat = proj
    rings_xy, bounds = [], []
    for ring in rings or []:
        pts = []
        for p in ring:
            x = (p["lon"] - lon0) * m_per_deg * cos_lat
            y = (p["lat"] - lat0) * m_per_deg
            pts.append((x * cos_t + y * sin_t, -x * sin_t + y * cos_t))
        rings_xy.append(pts)
        ys = [y for _, y in pts]
        bounds.append((min(ys), max(ys)))
    return rings_xy, bounds


# Coverage analysis samples the field on a grid. Resolution is tied to the
# swath so the answer means the same thing at any field size, and the total
# sample count is capped so a huge field cannot turn a plan request into a
# multi-second CPU burn.
_COVERAGE_SAMPLES_PER_SWATH = 4
_COVERAGE_MAX_SAMPLES = 120_000


def _coverage_stats(field_rot, segments, kp_rot, kp_ybounds,
                    buffer_m, swath_m, charge):
    """Measure what fraction of the SPRAYABLE field the passes actually cover.

    Sprayable = inside the field boundary and outside every buffered keepout,
    i.e. the ground we intended to spray. Keepout area is excluded rather than
    counted as a miss — not spraying a pond is the plan working, not a gap.

    Works in the rotated frame, where every pass is horizontal: a sample is
    covered iff some pass line lies within half a swath in y and the sample's
    x falls inside that pass's sprayed extent. Passes sit one swath apart, so
    at most two can be in range and the test is O(1) per sample rather than a
    scan over every segment.

    Returns {} when there is nothing meaningful to measure.
    """
    if not segments:
        return {}
    xs = [x for x, _ in field_rot]
    ys = [y for _, y in field_rot]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    if x1 <= x0 or y1 <= y0:
        return {}
    res = swath_m / _COVERAGE_SAMPLES_PER_SWATH
    # Coarsen rather than refuse if the field is enormous: an approximate
    # coverage number is useful, a 20-second one is not.
    while ((x1 - x0) / res) * ((y1 - y0) / res) > _COVERAGE_MAX_SAMPLES:
        res *= 2.0
    half = swath_m / 2.0

    # Bucket the sprayed extents by pass line (y), so lookup is by y.
    by_y: dict[float, list[tuple[float, float]]] = {}
    for (sx, sy), (ex, _ey) in segments:
        by_y.setdefault(sy, []).append((min(sx, ex), max(sx, ex)))
    pass_ys = sorted(by_y)

    sprayable = covered = 0
    y = y0 + res / 2.0
    while y <= y1:
        charge(len(field_rot))
        inside = _merge_intervals(list(_line_crossings(field_rot, y)))
        if inside:
            blocked = []
            for k, ring in enumerate(kp_rot):
                lo, hi = kp_ybounds[k]
                if y < lo - buffer_m or y > hi + buffer_m:
                    continue
                charge(len(ring))
                blocked.extend(_blocked_intervals(ring, y, buffer_m))
            open_iv = []
            for a, b in inside:
                open_iv.extend(_subtract_intervals(a, b,
                                                   _merge_intervals(blocked)))
            # Pass lines close enough in y to cover this row.
            near = [py for py in pass_ys if abs(py - y) <= half]
            for a, b in open_iv:
                x = a + res / 2.0
                while x <= b:
                    sprayable += 1
                    for py in near:
                        if any(lo <= x <= hi for lo, hi in by_y[py]):
                            covered += 1
                            break
                    x += res
        y += res

    if sprayable == 0:
        return {}
    cell = res * res
    return {
        "coverage_pct": round(100.0 * covered / sprayable, 1),
        "sprayable_acres": round(sprayable * cell / _M2_PER_ACRE, 2),
        "uncovered_acres": round((sprayable - covered) * cell / _M2_PER_ACRE, 2),
    }


def _reversal_offset(prev_seg, cand_seg):
    """Lateral room a turn from prev_seg onto cand_seg has, or None.

    None means the two are flown in the SAME direction, so the transition is a
    reposition rather than a reversal and the pass-spacing constraint does not
    apply to it (see the turn-geometry section for what that does and does not
    model). Passes are horizontal in the rotated frame, so the room available
    is the y separation.
    """
    if (prev_seg[1][0] - prev_seg[0][0]) * (cand_seg[1][0] - cand_seg[0][0]) >= 0:
        return None
    return abs(cand_seg[0][1] - prev_seg[1][1])


def _order_segments_around_hazards(segments, hulls, tol, charge,
                                   min_turn_spacing_m=0.0):
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

    min_turn_spacing_m keeps this from quietly undoing the turn-geometry
    ordering. Nearest-unflown is by definition the ADJACENT pass, so an
    unconstrained greedy re-tightens every turn back to one swath the moment a
    field has a hazard on it -- on exactly the fields where that matters most.
    Candidates with enough lateral room are therefore ranked first, and a
    too-tight one is taken only when nothing else is reachable.
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
        # Spacing is pure arithmetic, so it filters BEFORE the expensive
        # hazard test rather than after -- the tiering costs no extra
        # blocked() calls in the common case, and the cache keeps a fallback
        # sweep from charging the CPU budget twice for the same candidate.
        seen = {}

        def clear(i, rev):
            key = (i, rev)
            if key not in seen:
                seen[key] = not blocked(
                    pos, (remaining[i][1] if rev else remaining[i][0]))
            return seen[key]

        def oriented(i, rev):
            seg = remaining[i]
            return (seg[1], seg[0]) if rev else seg

        def roomy(i, rev):
            if min_turn_spacing_m <= 0.0:
                return True
            off = _reversal_offset(ordered[-1], oriented(i, rev))
            return off is None or off >= min_turn_spacing_m

        pick = None
        for tier in (True, False):
            for d, i, rev in cands:
                if tier and not roomy(i, rev):
                    continue
                if clear(i, rev):
                    pick = (d, i, rev)
                    break
            if pick is not None:
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

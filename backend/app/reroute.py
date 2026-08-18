"""Route a connecting leg around hazard keepouts.

WHY THIS EXISTS
---------------
The coverage planner clips SPRAY PASSES around keepouts, but the connecting
legs between them — in-field hops between sub-segments, inter-field transits,
and the home legs — have always flown straight through. That was a documented,
accepted limitation while every keepout was a spray-quality feature: overflying
a pond with the sprayer off wastes nothing and hurts nothing.

Powerline keepouts broke that assumption. A line is an AIRFRAME hazard: a low
ag pass crossing one is a crash, not a wasted pass. So hazard-kind keepouts get
their connecting legs rerouted around them, while spray-quality keepouts keep
the cheap straight-line behavior (rerouting around every treeline would add
flight time and battery for no safety gain).

APPROACH — convex-hull detours, deliberately not a visibility graph
-------------------------------------------------------------------
Each hazard ring is reduced to its CONVEX HULL, expanded by the clearance
buffer. Detours are then taut paths around that hull. This is over-conservative
by construction — hull(ring) contains ring, so any path clearing the hull
clears the real obstacle — which matches this codebase's standing rule that
over-standoff errs on the safe side, never the reverse.

The cost of that choice: a genuinely concave hazard (an L-shaped feeder line)
is treated as its filled hull, so the detour is longer than strictly necessary,
and a gap that exists inside the hull is not used. That is accepted. A full
visibility graph would find shorter paths but needs careful degenerate-case
handling around collinear and boundary-touching vertices, and shorter is not
the goal here — provably clear is.

FAILURE IS EXPLICIT, NEVER SILENT
---------------------------------
If a leg cannot be routed (an endpoint sits inside a hazard, or the iteration
or CPU budget runs out), route_leg returns None and the CALLER MUST keep
counting that leg as an unresolved overflight so the operator is warned. A
straight leg the operator knows about is survivable; a detour we quietly failed
to compute is not.

All geometry is in a flat local (x, y) meter frame supplied by the caller —
same equirectangular projection the coverage planner already works in.
"""

import math

# A detour path only has to CLEAR the hull, and a taut path RIDES its boundary:
# the detour vertices are hull vertices, and the legs between them lie exactly
# on hull edges. Half-plane clipping treats that closed boundary as inside, so
# without a penetration tolerance every taut detour would look like a fresh
# collision and rerouting could never converge. Require the segment to get
# strictly INSIDE by this much (metres) before it counts as entering — i.e.
# test against the hull shrunk by _PENETRATION_EPS_M.
_PENETRATION_EPS_M = 1e-6

# Iteration cap for the reroute loop. Each round resolves at least one blocking
# hull, so this bounds how tangled an area we will try to solve before giving
# up and reporting the leg unresolved.
_MAX_ROUNDS = 8

# The buffer disc is approximated by a CIRCUMSCRIBED regular polygon: one
# whose inscribed circle has radius r has circumradius r / cos(pi/n). Because
# it contains the disc, a path clearing it never gets closer than r.
#
# The side count is not cosmetic. Spray passes are clipped at EXACTLY the clip
# buffer, so a clipped pass endpoint sits exactly on the hazard boundary — and
# any circumscribed polygon bulges past that boundary by (f - 1) * r in the
# vertex directions, which puts those endpoints marginally INSIDE the hull.
# With n=8 that bulge is 8% of the buffer (1.6 m at 20 m), enough that every
# clipped endpoint reads as "inside a hazard" and NOTHING can be routed — the
# feature reports overflights and fixes none of them.
#
# n=32 shrinks the bulge to ~0.48% (under 10 cm at a 20 m buffer). That is the
# tolerance below, applied to both the endpoint test and the segment test, so
# worst-case delivered clearance is r - 0.0048*r instead of r. A ~5 cm
# shortfall on a 20 m lateral standoff is far inside the GPS error this whole
# projection already accepts, and it buys a feature that actually works.
_POLY_SIDES = 32
_POLY_FACTOR = 1.0 / math.cos(math.pi / _POLY_SIDES)
_POLY_DIRS = [(math.cos(a), math.sin(a))
              for a in (i * 2 * math.pi / _POLY_SIDES + math.pi / _POLY_SIDES
                        for i in range(_POLY_SIDES))]


def hull_tolerance(buffer_m: float) -> float:
    """How far a point at exactly buffer_m from the ring can sit inside the
    hull, purely from the polygon approximation. Callers pass this as the
    routing tolerance so boundary-clipped spray endpoints stay routable."""
    return max(0.0, buffer_m) * (_POLY_FACTOR - 1.0) + 1e-9


def _cross(o, a, b) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Monotone-chain convex hull, counter-clockwise, no repeated endpoint.

    Returns the input (deduplicated) when fewer than 3 distinct points survive
    — callers treat a degenerate hull as "nothing to route around".
    """
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts
    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def hazard_hull(ring: list[tuple[float, float]],
                buffer_m: float) -> list[tuple[float, float]]:
    """Convex hull of `ring` expanded outward by `buffer_m` of clearance.

    Every vertex is replaced by the corners of a circumscribed regular polygon
    of inradius buffer_m around it, then hulled. Because that polygon contains
    the disc, hull(ring (+) polygon) contains hull(ring) (+) disc — clearance
    is never less than requested, up to the documented hull_tolerance.

    A corridor ring (a line traced out and back, which is how waterways and
    power lines arrive) hulls into a capsule-ish polygon around the line —
    exactly the shape a lateral-clearance detour should go around.
    """
    if not ring:
        return []
    r = max(0.0, buffer_m) * _POLY_FACTOR
    if r == 0.0:
        return convex_hull(list(ring))
    expanded = [(x + dx * r, y + dy * r)
                for (x, y) in ring for (dx, dy) in _POLY_DIRS]
    return convex_hull(expanded)


def segment_enters_hull(a, b, hull, eps_m: float = _PENETRATION_EPS_M) -> bool:
    """True if segment a-b penetrates convex `hull` by more than `eps_m`.

    Exact half-plane clipping against the hull shrunk by eps_m: the segment is
    intersected with every edge's inner half-plane, yielding the parametric
    interval of the segment inside the polygon. Convexity makes this exact and
    cheap, and the eps_m inset is what lets a taut detour ride the boundary
    without reporting a collision — the reason this routine is convex-only.

    Normals are UNIT normals so eps_m is a real distance in metres rather than
    a value scaled by each edge's length.
    """
    if len(hull) < 3:
        return False
    dx, dy = b[0] - a[0], b[1] - a[1]
    seg_len = math.hypot(dx, dy)
    if seg_len == 0.0:
        return False
    t0, t1 = 0.0, 1.0
    n = len(hull)
    for i in range(n):
        px, py = hull[i]
        qx, qy = hull[(i + 1) % n]
        # Hull is CCW, so the interior lies to the LEFT of p->q; the outward
        # normal is the right-hand perpendicular, normalized.
        ex, ey = qx - px, qy - py
        elen = math.hypot(ex, ey)
        if elen == 0.0:
            continue
        nx, ny = ey / elen, -ex / elen
        # Inside the SHRUNK hull means n.(x-p) <= -eps_m.
        num = nx * (a[0] - px) + ny * (a[1] - py) + eps_m
        den = nx * dx + ny * dy
        if abs(den) < 1e-15:
            if num > 0.0:
                return False        # parallel and fully outside this edge
            continue
        t = -num / den
        if den < 0.0:               # entering the half-plane
            if t > t0:
                t0 = t
        else:                       # leaving it
            if t < t1:
                t1 = t
        if t0 > t1:
            return False
    return (t1 - t0) * seg_len > 0.0


def _taut_chain(pts, side: int):
    """Reduce a side-ordered point list to the taut (convex) chain.

    `pts` starts at `a`, ends at `b`, with candidate hull vertices between,
    already sorted along a->b. Dropping any vertex that bends the wrong way
    leaves the shortest path on that side — the same discard rule as a Graham
    scan, applied to one side only.
    """
    chain: list[tuple[float, float]] = []
    for p in pts:
        while len(chain) >= 2 and _cross(chain[-2], chain[-1], p) * side >= 0:
            chain.pop()
        chain.append(p)
    return chain


def _side_detour(a, b, hull, side: int):
    """Taut path from a to b passing on one side of `hull`, or None."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    seg_sq = dx * dx + dy * dy
    if seg_sq == 0.0:
        return None
    picked = []
    for p in hull:
        if _cross(a, b, p) * side > 0:      # strictly on the chosen side
            t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / seg_sq
            picked.append((t, p))
    if not picked:
        return None
    picked.sort(key=lambda tp: tp[0])
    chain = _taut_chain([a] + [p for _, p in picked] + [b], side)
    return chain if len(chain) > 2 else None


def _path_len(path) -> float:
    return sum(math.dist(path[i - 1], path[i]) for i in range(1, len(path)))


def route_leg(a, b, hulls, charge=None, tol_m: float = 0.0,
              max_extra_m: float = math.inf):
    """Route the leg a->b around every hazard hull it would otherwise enter.

    Returns the list of INTERMEDIATE waypoints (never including a or b), which
    is empty when the straight leg is already clear. Returns None when the leg
    cannot be resolved — an endpoint inside a hazard, hulls too tangled for the
    round cap, or no clear side — and the caller must then keep the straight
    leg AND report it as an unresolved overflight.

    `charge(n)` is the caller's CPU-budget hook (the coverage planner shares
    one budget across a whole job); it may raise to abort an over-budget plan.

    `tol_m` is the polygon-approximation slack from hull_tolerance(): a point
    within that distance of the hull boundary counts as ON it, not inside.
    Without it, spray endpoints clipped at exactly the buffer distance read as
    inside the hazard and every leg becomes unroutable.

    `max_extra_m` caps how much distance a detour may ADD over the straight
    leg; beyond it the leg is reported unresolved instead. This is not a
    performance guard, it is a correctness one. Hazards here are power lines,
    which are LINEAR and effectively unbounded — a real transmission line runs
    for kilometres past the field. Going "around" one means flying to the end
    of the line, so an unbounded router happily turns a 50 m hop into a 5 km
    detour (measured against a real 115 kV Evergy line). No operator would fly
    that. When the detour is absurd the honest output is "this plan crosses
    the line here, deal with it", not a path nobody will follow.
    """
    if not hulls:
        return []
    # An endpoint genuinely inside a hazard cannot be routed away from: the
    # spray pattern itself would have to move. Report it rather than inventing
    # a path. Marginal penetration within tol_m is the approximation, not a
    # real incursion, so it does not disqualify the leg.
    for hull in hulls:
        if (_hull_penetration(a, hull) > tol_m
                or _hull_penetration(b, hull) > tol_m):
            return None

    path = [a, b]
    for _ in range(_MAX_ROUNDS):
        blocking = None
        for i in range(1, len(path)):
            for hull in hulls:
                if charge:
                    charge(len(hull))
                if segment_enters_hull(path[i - 1], path[i], hull,
                                       eps_m=max(tol_m, _PENETRATION_EPS_M)):
                    blocking = (i, hull)
                    break
            if blocking:
                break
        if blocking is None:
            if math.isfinite(max_extra_m):
                extra = _path_len(path) - math.dist(a, b)
                if extra > max_extra_m:
                    # A clear path exists but it is not one anybody would fly.
                    return None
            return path[1:-1]           # clear: hand back the detour points
        i, hull = blocking
        p, q = path[i - 1], path[i]
        best = None
        for side in (1, -1):
            cand = _side_detour(p, q, hull, side)
            if cand is None:
                continue
            # A side is only usable if it actually clears THIS hull; the loop
            # re-checks the others on the next round.
            if any(segment_enters_hull(cand[j - 1], cand[j], hull,
                                       eps_m=max(tol_m, _PENETRATION_EPS_M))
                   for j in range(1, len(cand))):
                continue
            length = _path_len(cand)
            if best is None or length < best[0]:
                best = (length, cand)
        if best is None:
            return None                 # boxed in on both sides
        path = path[:i - 1] + best[1] + path[i + 1:]
    return None                         # too tangled within the round cap


def _hull_penetration(p, hull) -> float:
    """How far inside CCW convex `hull` point p lies, in metres.

    Positive = inside by that distance; <= 0 = on or outside. Distances are
    real metres because the edge normals are normalized, which is what lets
    callers compare against a metric tolerance.
    """
    if len(hull) < 3:
        return 0.0
    depth = math.inf
    n = len(hull)
    for i in range(n):
        px, py = hull[i]
        qx, qy = hull[(i + 1) % n]
        ex, ey = qx - px, qy - py
        elen = math.hypot(ex, ey)
        if elen == 0.0:
            continue
        # Outward normal; negative dot == inside this edge.
        d = -(((ey / elen) * (p[0] - px)) + ((-ex / elen) * (p[1] - py)))
        if d < depth:
            depth = d
    return 0.0 if depth is math.inf else depth


def _point_in_hull(p, hull) -> bool:
    """Strictly-inside test for a CCW convex hull (boundary counts as out)."""
    return _hull_penetration(p, hull) > 0.0

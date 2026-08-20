"""Multi-field spray-job planning.

Composes the single-field keepout-aware planner (app.coverage.plan_coverage)
into a whole job: several fields, visited in a transit-efficient order, with
explicit inter-field transit legs so the UI can show the complete flight —
spray passes AND everything between them.

Ordering: greedy nearest-endpoint tour starting from `home` (or the first
field). Each field's serpentine can be flown from either end, so the tour may
REVERSE a field's waypoints when entering from the far end shortens transit.

Transit legs fly at spray altitude between pattern endpoints, and every transit
point STATES that altitude so no consumer has to guess it. They are NOT
rerouted around ordinary keepouts — overflying a pond with the sprayer off
costs nothing — but they ARE rerouted around `hazards` (powerlines), because
crossing one of those is a crash rather than a wasted pass. Before this,
inter-field transits and the home legs were not even CHECKED for crossings;
only in-field hops were counted.
"""

import math

from app.coverage import (_MAX_CLIP_WORK, _detour_budget_m, _project, _unproject,
                          DEFAULT_MAX_BANK_DEG, EARTH_RADIUS_M, plan_coverage)
from app.reroute import hazard_hull, hull_tolerance, route_leg

# Same meters-per-degree as the single-field planner, so spray lengths and
# transit lengths summed into one total are on the same scale.
_M_PER_DEG = math.pi / 180.0 * EARTH_RADIUS_M


def _dist_m(a: dict, b: dict) -> float:
    """Equirectangular distance in meters — fine at job scale."""
    kx = math.cos(math.radians((a["lat"] + b["lat"]) / 2.0)) * _M_PER_DEG
    return math.hypot((a["lat"] - b["lat"]) * _M_PER_DEG, (a["lon"] - b["lon"]) * kx)


def _coverage_totals(planned: dict) -> dict:
    """Aggregate per-field coverage into job totals, area-weighted."""
    sprayable = sum(p["stats"].get("sprayable_acres", 0.0) for p in planned.values())
    uncovered = sum(p["stats"].get("uncovered_acres", 0.0) for p in planned.values())
    if sprayable <= 0:
        return {}
    return {
        "sprayable_acres": round(sprayable, 2),
        "uncovered_acres": round(uncovered, 2),
        "coverage_pct": round(100.0 * (sprayable - uncovered) / sprayable, 1),
    }


def plan_multi(fields, swath_m, alt_m, keepouts=None, keepout_buffer_m=0.0,
               home=None, speed_ms=18.0, hazards=None, hazard_buffer_m=0.0,
               max_bank_deg=DEFAULT_MAX_BANK_DEG, headlands=True):
    """Plan a multi-field job.

    fields: list of polygons (each a list of {lat,lon}). Fields that fail to
    plan (fully blocked / degenerate) are reported in `skipped`, never fatal
    unless NO field survives.

    headlands: per-field pass widening, passed straight through to
    plan_coverage.

    max_bank_deg: per-field turn-geometry bank ceiling, passed straight
    through to plan_coverage. Transit legs BETWEEN fields are not constrained
    by it — they are single repositions at altitude, not the hundreds of
    low-level turnarounds the constraint exists for.

    hazards: keepout rings that must be FLOWN AROUND rather than overflown
    (powerlines). Applied to in-field hops by plan_coverage and to the transit
    and home legs here, with hazard_buffer_m of lateral clearance. A leg that
    cannot be routed keeps its straight path and is counted in
    totals.hazard_overflights so the operator is warned.

    Returns {fields, flight_order, transits, combined_waypoints, totals,
             skipped}. Each transit carries "kind" ("direct" or "detour") and
    its full point list; combined_waypoints includes detour points, and
    combined_leg_kinds labels every consecutive pair.
    """
    if not fields:
        raise ValueError("at least one field is required")

    planned = {}   # original index -> plan dict
    skipped = []
    # ONE clip-work budget for the whole job: without it each of up to 25
    # fields gets its own _MAX_CLIP_WORK allowance and a single request can
    # burn ~a minute of GIL-bound CPU (starving telemetry) instead of ~2 s.
    work_budget = [_MAX_CLIP_WORK]
    for i, poly in enumerate(fields):
        try:
            kwargs = {}
            if keepouts is not None:
                kwargs = {"keepouts": keepouts, "keepout_buffer_m": keepout_buffer_m,
                          "work_budget": work_budget}
            if hazards:
                kwargs["hazards"] = hazards
                kwargs["hazard_buffer_m"] = hazard_buffer_m
                # Hazard rerouting draws on the same job-wide CPU allowance.
                kwargs.setdefault("work_budget", work_budget)
            plan = plan_coverage(poly, swath_m, alt_m, speed_ms=speed_ms,
                                 max_bank_deg=max_bank_deg,
                                 headlands=headlands, **kwargs)
            if plan["waypoints"]:
                planned[i] = plan
            else:
                skipped.append({"index": i, "error": "plan produced no waypoints"})
        except ValueError as e:
            skipped.append({"index": i, "error": str(e)})

    if not planned:
        raise ValueError("no plannable fields in the job")

    # --- Greedy nearest-endpoint tour with optional per-field reversal ---
    pos = dict(home) if home else planned[sorted(planned)[0]]["waypoints"][0]
    remaining = set(planned)
    flight_order = []   # [{index, reversed}]
    while remaining:
        best = None  # (dist, index, reversed)
        for i in remaining:
            wps = planned[i]["waypoints"]
            d_fwd = _dist_m(pos, wps[0])
            d_rev = _dist_m(pos, wps[-1])
            if best is None or d_fwd < best[0]:
                best = (d_fwd, i, False)
            if d_rev < best[0]:
                best = (d_rev, i, True)
        _, idx, rev = best
        remaining.discard(idx)
        flight_order.append({"index": idx, "reversed": rev})
        wps = planned[idx]["waypoints"]
        pos = wps[0] if rev else wps[-1]  # exit at the other end

    # --- Hazard hulls for the transit/home legs -----------------------------
    # One projection for the WHOLE job (the per-field ones are centred on each
    # field and would disagree between them). Transit legs span fields, so
    # they must be routed in a single shared frame.
    job_pts = [p for f in fields for p in f]
    if home:
        job_pts = job_pts + [home]
    _, job_proj = _project(job_pts)
    transit_hulls = []
    if hazards:
        lat0, lon0, m_per_deg, cos_lat = job_proj
        for hz in hazards:
            ring = [((p["lon"] - lon0) * m_per_deg * cos_lat,
                     (p["lat"] - lat0) * m_per_deg) for p in hz]
            hull = hazard_hull(ring, hazard_buffer_m)
            if len(hull) >= 3:
                transit_hulls.append(hull)

    def _charge(n):
        work_budget[0] -= n
        if work_budget[0] < 0:
            raise ValueError(
                "keepout clipping too complex for this request: "
                "increase the swath, shrink the job, or simplify the keepouts")

    transit_overflights = 0
    transit_reroutes = 0

    def _leg(a, b, origin):
        """Build one transit leg from a to b, routed around hazards.

        Returns the leg dict; its "pts" carry any detour vertices, so the UI
        draws — and the mission flies — the path actually planned.

        Every point STATES its altitude: the spray altitude, which is what the
        aircraft has always flown here. It was previously left unset, so each
        consumer guessed — MapView3D.jsx guessed 60 m and drew a climb between
        fields that never happens, while SprayPanel.jsx's upload backfill
        guessed the panel's spray altitude and was right by accident. Stating
        it gives the number one home; both of those consumers now read it.

        This does NOT decide whether a transit SHOULD have its own altitude
        (seam S7) — that is Tabor + AIR's call. Do not invent a different
        number here to answer it.
        """
        nonlocal transit_overflights, transit_reroutes
        pts = [{"lat": a["lat"], "lon": a["lon"], "alt": alt_m},
               {"lat": b["lat"], "lon": b["lon"], "alt": alt_m}]
        kind = "direct"
        if transit_hulls:
            lat0, lon0, m_per_deg, cos_lat = job_proj
            pa = ((a["lon"] - lon0) * m_per_deg * cos_lat,
                  (a["lat"] - lat0) * m_per_deg)
            pb = ((b["lon"] - lon0) * m_per_deg * cos_lat,
                  (b["lat"] - lat0) * m_per_deg)
            detour = route_leg(pa, pb, transit_hulls, charge=_charge,
                               tol_m=hull_tolerance(hazard_buffer_m),
                               max_extra_m=_detour_budget_m(
                                   math.dist(pa, pb)))
            if detour is None:
                # Straight leg kept, but never silently — the operator is told.
                transit_overflights += 1
            elif detour:
                transit_reroutes += 1
                kind = "detour"
                mid = []
                for (x, y) in detour:
                    dlat, dlon = _unproject(x, y, job_proj)
                    mid.append({"lat": dlat, "lon": dlon, "alt": alt_m})
                pts = ([pts[0]] + mid + [pts[-1]])
        length = sum(_dist_m(pts[i - 1], pts[i]) for i in range(1, len(pts)))
        return {"from": origin, "pts": pts, "length_m": round(length, 1),
                "kind": kind}, length

    # --- Stitch: combined waypoints + explicit transit legs ---
    combined = []
    combined_leg_kinds = []
    transits = []
    transit_len = 0.0
    pos = dict(home) if home else None

    def _extend(points, kinds):
        """Append points to the combined mission, labelling each new leg."""
        for i, pt in enumerate(points):
            if combined:
                combined_leg_kinds.append(kinds[i] if i < len(kinds) else "hop")
            combined.append(pt)

    for stop in flight_order:
        wps = list(planned[stop["index"]]["waypoints"])
        kinds = list(planned[stop["index"]].get("leg_kinds") or [])
        if stop["reversed"]:
            wps = wps[::-1]
            kinds = kinds[::-1]   # leg i joins wp i and i+1; reversing both keeps them aligned
        entry = wps[0]
        if pos is not None:
            # The very first leg (nothing stitched yet) departs home; every
            # later leg departs the previous field's exit.
            leg, leg_len = _leg(pos, entry,
                                "home" if (home and not combined) else "field")
            transits.append(leg)
            transit_len += leg_len
            # The transit's intermediate points are real waypoints too.
            _extend(leg["pts"][1:-1], ["transit"] * len(leg["pts"][1:-1]))
            _extend([entry], ["transit"])
            _extend(wps[1:], kinds)
        else:
            _extend(wps, [None] + kinds)
        pos = wps[-1]
    home_leg_hazard = False
    if home:
        leg, leg_len = _leg(pos, home, "field")
        transits.append(leg)
        transit_len += leg_len
        # DELIBERATELY NOT added to combined_waypoints. The return home is not
        # part of the commanded mission — the mission ends at the last field
        # waypoint and the aircraft returns under autopilot RTL, which flies
        # STRAIGHT home and knows nothing about our keepouts. Appending detour
        # points here would be worse than useless: the aircraft would fly out
        # to a detour vertex and then RTL straight back across the hazard.
        # So the home leg's route is computed for DISPLAY and for the warning
        # below, and the operator is told that RTL will not avoid it.
        home_leg_hazard = leg["kind"] == "detour" or transit_overflights > 0

    spray_len = sum(planned[i]["stats"]["path_length_m"] for i in planned)
    area_acres = sum(planned[i]["stats"]["area_acres"] for i in planned)
    total_len = spray_len + transit_len

    return {
        "fields": [
            {"index": i, "waypoints": planned[i]["waypoints"], "stats": planned[i]["stats"]}
            for i in sorted(planned)
        ],
        "flight_order": flight_order,
        "transits": transits,
        "combined_waypoints": combined,
        "totals": {
            "fields": len(planned),
            "area_acres": round(area_acres, 2),
            "spray_path_m": round(spray_len, 1),
            "transit_m": round(transit_len, 1),
            "total_m": round(total_len, 1),
            "est_time_s": round(total_len / speed_ms, 1),
            "waypoints": len(combined),
            # Connecting legs that physically cross a keepout polygon —
            # the aircraft overflies the zone there (sprayer must be off;
            # mind trees at spray altitude). The UI warns when > 0.
            "keepout_overflights": sum(
                planned[i]["stats"].get("keepout_overflights", 0)
                for i in planned),
            # Legs actually routed around a hazard (in-field hops + transits).
            "hazard_reroutes": sum(
                planned[i]["stats"].get("hazard_reroutes", 0)
                for i in planned) + transit_reroutes,
            # THE number that matters: legs that still cross a hazard because
            # routing could not resolve them. Any value > 0 is an operator
            # warning, not a statistic.
            "hazard_overflights": sum(
                planned[i]["stats"].get("hazard_overflights", 0)
                for i in planned) + transit_overflights,
            # Job-wide coverage: how much of the ground we intended to
            # spray the passes actually hit. Aggregated by AREA, not by
            # averaging per-field percentages — a 2-acre field missing half
            # of itself must not weigh the same as a 200-acre field.
            **_coverage_totals(planned),
            # The RTL path home crosses a hazard. RTL is autopilot-controlled
            # and flies straight, so we cannot route around it — the operator
            # has to know before launching.
            "home_leg_hazard": home_leg_hazard,
        },
        "combined_leg_kinds": combined_leg_kinds,
        "skipped": skipped,
    }

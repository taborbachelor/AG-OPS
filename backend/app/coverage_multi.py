"""Multi-field spray-job planning.

Composes the single-field keepout-aware planner (app.coverage.plan_coverage)
into a whole job: several fields, visited in a transit-efficient order, with
explicit inter-field transit legs so the UI can show the complete flight —
spray passes AND everything between them.

Ordering: greedy nearest-endpoint tour starting from `home` (or the first
field). Each field's serpentine can be flown from either end, so the tour may
REVERSE a field's waypoints when entering from the far end shortens transit.

Transit legs are straight lines and are NOT rerouted around keepouts (same
documented limitation as in-field hops) — they fly at spray altitude between
pattern endpoints.
"""

import math

from app.coverage import _MAX_CLIP_WORK, EARTH_RADIUS_M, plan_coverage

# Same meters-per-degree as the single-field planner, so spray lengths and
# transit lengths summed into one total are on the same scale.
_M_PER_DEG = math.pi / 180.0 * EARTH_RADIUS_M


def _dist_m(a: dict, b: dict) -> float:
    """Equirectangular distance in meters — fine at job scale."""
    kx = math.cos(math.radians((a["lat"] + b["lat"]) / 2.0)) * _M_PER_DEG
    return math.hypot((a["lat"] - b["lat"]) * _M_PER_DEG, (a["lon"] - b["lon"]) * kx)


def plan_multi(fields, swath_m, alt_m, keepouts=None, keepout_buffer_m=0.0,
               home=None, speed_ms=18.0):
    """Plan a multi-field job.

    fields: list of polygons (each a list of {lat,lon}). Fields that fail to
    plan (fully blocked / degenerate) are reported in `skipped`, never fatal
    unless NO field survives.

    Returns {fields, flight_order, transits, combined_waypoints, totals,
             skipped}.
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
            plan = plan_coverage(poly, swath_m, alt_m, speed_ms=speed_ms, **kwargs)
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

    # --- Stitch: combined waypoints + explicit transit legs ---
    combined = []
    transits = []
    transit_len = 0.0
    pos = dict(home) if home else None
    for stop in flight_order:
        wps = list(planned[stop["index"]]["waypoints"])
        if stop["reversed"]:
            wps = wps[::-1]
        entry = wps[0]
        if pos is not None:
            # The very first leg (nothing stitched yet) departs home; every
            # later leg departs the previous field's exit.
            leg_len = _dist_m(pos, entry)
            transits.append({
                "from": "home" if (home and not combined) else "field",
                "pts": [{"lat": pos["lat"], "lon": pos["lon"]},
                        {"lat": entry["lat"], "lon": entry["lon"]}],
                "length_m": round(leg_len, 1),
            })
            transit_len += leg_len
        combined.extend(wps)
        pos = wps[-1]
    if home:
        leg_len = _dist_m(pos, home)
        transits.append({
            "from": "field",
            "pts": [{"lat": pos["lat"], "lon": pos["lon"]},
                    {"lat": home["lat"], "lon": home["lon"]}],
            "length_m": round(leg_len, 1),
        })
        transit_len += leg_len

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
        },
        "skipped": skipped,
    }

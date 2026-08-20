"""Rally points so a link-loss RTL diverts to a safe alternate instead of
flying straight home through a mapped hazard corridor.

Companion to `onboard_fence.py`. Fences stop the aircraft ENTERING a hazard;
rally points give link-loss RTL somewhere correct to fly TO instead of home --
ArduPilot's RTL logic already prefers the nearest enabled rally point over
home once one exists (RALLY_TOTAL > 0), so uploading a rally point that is
provably clear of every mapped hazard -- and reachable from home on a clear
straight line -- is enough to change what a link-loss RTL actually does. No
onboard rerouting exists for a rally leg (unlike `reroute.py`'s connector
legs), so BOTH ends of that leg have to already be clear.

Candidate rally points are supplied by the caller (operator-picked on the
map, or a default computed elsewhere) -- this module does not invent a
location, it only validates one. Two independent hazard checks, either of
which refuses the WHOLE candidate set outright rather than uploading a rally
point that only looks safe -- same "never a partial, confidently-wrong
result" discipline `build_exclusion_items` uses:

1. The point itself must clear every hazard ring by RALLY_CLEARANCE_M --
   same idea as onboard_fence's HOME_CLEARANCE_M check on home.
2. The straight leg from HOME to the rally point must also clear every
   hazard by the same margin. A rally point that is itself clear but sits on
   the far side of a hazard from home would just move where the powerline
   gets hit, not remove it. Checked by sampling the leg at a bounded
   interval and reusing the same point-to-hazard distance primitive.

Pure geometry and plain dicts -- no pymavlink, no vehicle, no network -- so
it unit-tests standalone, mirroring `onboard_fence.py`'s split.
`vehicle_manager` maps the items onto MAVLink.
"""

import math

from app.gis_zones import dist_to_zone_m

# MAV_CMD_NAV_RALLY_POINT. Hardcoded rather than pulled from mavutil so this
# module stays import-light and testable without a dialect -- same reasoning
# as onboard_fence's FENCE_POLYGON_VERTEX_EXCLUSION constant.
NAV_RALLY_POINT = 5100

# Minimum clearance a rally point -- and the straight home<->rally leg -- must
# keep from every hazard ring. Matches onboard_fence.HOME_CLEARANCE_M: a rally
# point closer than this to a mapped powerline is not meaningfully safer than
# flying straight home through it.
RALLY_CLEARANCE_M = 30.0

# Rally storage on the flight controller is small (ArduPilot's default
# RALLY_TOTAL cap is 10 points on most boards, sharing EEPROM with missions
# and fences the same way onboard_fence.MAX_FENCE_POINTS documents). Refuse
# rather than silently truncate.
MAX_RALLY_POINTS = 10

# Sampling step along the home<->rally leg for the crossing check. Bounded so
# a rally point picked far from home still costs a fixed number of
# dist_to_zone_m calls rather than growing unbounded.
_MAX_LEG_SAMPLES = 300
_MIN_SAMPLE_STEP_M = 5.0

_M_PER_DEG_LAT = 111_320.0


def _leg_length_m(a: dict, b: dict) -> float:
    """Straight-line distance between two {lat, lon} points, meters."""
    ref_lat_rad = math.radians(a["lat"])
    dx = (b["lon"] - a["lon"]) * _M_PER_DEG_LAT * math.cos(ref_lat_rad)
    dy = (b["lat"] - a["lat"]) * _M_PER_DEG_LAT
    return math.hypot(dx, dy)


def _sample_leg(a: dict, b: dict) -> list[dict]:
    """Points along a-b (inclusive of both ends), spaced no closer than
    _MIN_SAMPLE_STEP_M apart and bounded to _MAX_LEG_SAMPLES total."""
    length = _leg_length_m(a, b)
    if length <= 0.0:
        return [a, b]
    n = min(_MAX_LEG_SAMPLES, max(1, math.ceil(length / _MIN_SAMPLE_STEP_M)))
    return [{"lat": a["lat"] + (b["lat"] - a["lat"]) * i / n,
             "lon": a["lon"] + (b["lon"] - a["lon"]) * i / n}
            for i in range(n + 1)]


def _nearest_hazard_m(pt: dict, hazards: list[dict]):
    """(distance_m, kind) to the nearest hazard ring, (inf, None) if none."""
    best, kind = math.inf, None
    for ring in hazards:
        d = dist_to_zone_m(pt, {"coords": ring["coords"]})
        if d < best:
            best, kind = d, ring.get("kind", "unknown")
    return best, kind


def build_rally_items(candidates: list[dict], prepared: dict, home: dict | None,
                      clearance_m: float = RALLY_CLEARANCE_M,
                      max_points: int = MAX_RALLY_POINTS) -> dict:
    """Validate candidate rally points against hazard rings and shape them
    for MISSION_TYPE_RALLY transfer.

    `candidates` is a list of {lat, lon, alt, break_alt?, land_dir?} --
    break_alt/land_dir default to alt/0.0 when omitted. `prepared` is what
    `keepout_watch.prepare()` returns. `home` ({lat, lon}) is required --
    without it the leg-clearance check has nothing to check against, so
    every candidate is refused rather than silently skipping the check that
    gives rally points their whole point.

    Returns {items, points, refused}. Raises ValueError when any candidate
    fails either hazard check -- never returns a subset that silently
    dropped an unsafe candidate, because a rally point the operator thinks
    exists but doesn't upload is worse than one that was never proposed.
    """
    hazards = [r for r in (prepared or {}).get("rings") or [] if r.get("hazard")]

    if home is None:
        raise ValueError(
            "home position unknown -- cannot verify the home<->rally leg "
            "clears mapped hazards. Refusing rather than uploading an "
            "unverified rally point.")

    items: list[dict] = []
    refused: list[str] = []

    for i, c in enumerate(candidates):
        label = f"candidate {i} ({c['lat']:.6f}, {c['lon']:.6f})"
        pt = {"lat": c["lat"], "lon": c["lon"]}

        d, kind = _nearest_hazard_m(pt, hazards)
        if d < clearance_m:
            refused.append(
                f"{label}: {d:.0f} m from a {kind} hazard, under the "
                f"{clearance_m:.0f} m clearance")
            continue

        # The point itself is clear; ArduPilot flies the rally leg as a
        # straight line with no onboard rerouting, so the leg from home has
        # to be clear too.
        worst, worst_kind = math.inf, None
        for sample in _sample_leg(home, pt):
            sd, sk = _nearest_hazard_m(sample, hazards)
            if sd < worst:
                worst, worst_kind = sd, sk
        if worst < clearance_m:
            refused.append(
                f"{label}: the home<->rally leg passes within {worst:.0f} m "
                f"of a {worst_kind} hazard, under the {clearance_m:.0f} m "
                f"clearance")
            continue

        alt = float(c["alt"])
        # An omitted optional arrives in TWO shapes and both must mean "default".
        # Unit callers pass plain dicts with the key absent, which `.get(k, d)`
        # handles. The HTTP path does not: `RallyCandidate.model_dump()` ALWAYS
        # emits the key, carrying None when the operator omitted it -- so `.get`
        # returns that None, float(None) raises, and POST /api/safety/keepouts
        # 500s on its own documented default. Measured 2026-08-19 against SITL.
        break_alt = c.get("break_alt")
        land_dir = c.get("land_dir")
        items.append({
            "command": NAV_RALLY_POINT,
            "lat": pt["lat"], "lon": pt["lon"],
            "alt": alt,
            "break_alt": alt if break_alt is None else float(break_alt),
            "land_dir": 0.0 if land_dir is None else float(land_dir),
        })

    if refused:
        raise ValueError(
            "cannot build a verified rally set: " + "; ".join(refused)
            + ". Refusing rather than uploading an unverified rally point.")

    if len(items) > max_points:
        raise ValueError(
            f"{len(items)} rally points exceeds the {max_points}-point "
            f"limit. Reduce the candidate list or raise MAX_RALLY_POINTS "
            f"against a real measurement.")

    return {"items": items, "points": len(items), "refused": refused}

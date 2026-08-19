"""Turn planner keepouts into ArduPilot polygon exclusion fences.

Until now keepouts existed ONLY in the GCS. `coverage.py` clipped spray passes
around them, `reroute.py` routed connecting legs around hazards, and
`keepout_watch.py` warned when the flown position got close -- but all three run
on the laptop. Drop the link and every one of them goes silent, at exactly the
moment you would most want them. The aircraft itself had no idea the powerline
was there.

This module closes that gap: it converts the same rings into MISSION_TYPE_FENCE
polygon exclusion items, so the autopilot enforces them onboard with no link, no
companion computer, and no GCS running at all.

Four decisions worth knowing before you change anything here.

**Only HAZARDS become hard fences.** `keepout_watch` already splits rings into
airframe hazards (powerlines) and spray-quality keepouts (water/trees/
buildings), and that split matters even more here than it does there. A fence
breach fires FENCE_ACTION -- RTL, or land, depending on configuration. Making a
farm pond a hard fence would trigger a failsafe for a harmless overflight with
the sprayer off, and worse, a pond sitting between the field and home could
block RTL and turn a nuisance into an emergency. Hazards are the set where
"never enter this, whatever else is happening" is actually true.

**The ring is uploaded raw, not buffered.** The planner's `keepout_buffer` is a
SOFT standoff that keeps planned passes well clear; this fence is the HARD floor
at the true hazard boundary. Same philosophy the bench kit already uses for
guardian thresholds -- the GCS warns first and the autopilot is the last line,
never the other way round. Powerline rings arrive from `gis_zones` as corridors
already built around the conductor, so the hard boundary carries its own margin
regardless.

**Overflow fails loud.** Fence storage on the flight controller is tiny next to
what the monitor can hold -- `keepout_watch.MAX_RINGS` is 4000, and a single
Topeka ring measured 4,952 vertices, against a few hundred fence points total.
Truncating to fit would upload a fence that looks complete and silently omits a
wire. `keepout_watch` learned this same lesson by measurement: truncation
changed the answer rather than just the runtime. So we refuse and say what
would not fit.

**Built from the monitor's own prepared rings.** `build_exclusion_items` takes
the dict `keepout_watch.prepare()` returns, so the hard fence and the soft
proximity monitor are physically incapable of describing different geometry.
That is the "defaults on both sides that must agree" failure class solved by
construction instead of by discipline.

Pure geometry and plain dicts -- no pymavlink, no vehicle, no network -- so it
unit-tests standalone. `vehicle_manager` maps the items onto MAVLink.
"""

from app.gis_zones import dist_to_zone_m

# MAV_CMD_NAV_FENCE_POLYGON_VERTEX_EXCLUSION. Hardcoded rather than pulled from
# mavutil so this module stays import-light and testable without a dialect; the
# value is fixed by the MAVLink common message set.
FENCE_POLYGON_VERTEX_EXCLUSION = 5002

# Total fence VERTICES we are willing to upload. Deliberately conservative:
# ArduPilot stores fence points in the same backing store as missions and rally
# points, and the usable ceiling varies by board and firmware. Exceeding it
# fails the transfer partway, which is the one outcome worse than refusing
# up front. Raise it only against a measurement on the real Cube, not a
# datasheet -- and see MAX_VERTICES_PER_POLYGON before you do.
MAX_FENCE_POINTS = 200

# A single OSM ring can be pathological (4,952 vertices measured at Topeka).
# One such ring would eat the entire budget, so cap per polygon as well and
# report it as a refusal rather than quietly simplifying geometry that protects
# an airframe.
MAX_VERTICES_PER_POLYGON = 64

# Minimum separation between home and any exclusion boundary. Home sitting
# inside -- or right on the edge of -- an exclusion means a fence breach the
# instant the fence is enabled, which on most configurations means an immediate
# RTL to the point that is already breaching. Refuse instead.
HOME_CLEARANCE_M = 30.0


def _open_ring(coords: list[dict]) -> list[dict]:
    """Drop a repeated closing vertex.

    `gis_zones._close_ring` returns rings whose last point repeats the first,
    which is what the distance primitives expect. ArduPilot polygons are
    implicitly closed, so shipping the duplicate would add a zero-length edge
    and burn a point of a very small budget.
    """
    if len(coords) >= 2:
        a, b = coords[0], coords[-1]
        if abs(a["lat"] - b["lat"]) < 1e-12 and abs(a["lon"] - b["lon"]) < 1e-12:
            return coords[:-1]
    return list(coords)


def build_exclusion_items(prepared: dict, home: dict | None = None,
                          max_points: int = MAX_FENCE_POINTS) -> dict:
    """Build polygon exclusion fence items from prepared keepout rings.

    `prepared` is what `keepout_watch.prepare()` returns. `home` is optional
    {lat, lon}; when given, an exclusion containing (or within
    HOME_CLEARANCE_M of) it is refused rather than uploaded.

    Returns {items, polygons, points, skipped, refused}. `items` is ordered and
    ready for MISSION_TYPE_FENCE transfer: one item per vertex, each carrying
    the vertex count of the polygon it belongs to, which is how ArduPilot knows
    where one polygon ends and the next begins.

    Raises ValueError when the fence cannot be built correctly -- never returns
    a partial fence, because a fence that silently omits a wire is worse than
    no fence at all (an operator trusts the former).
    """
    rings = (prepared or {}).get("rings") or []
    hazards = [r for r in rings if r.get("hazard")]
    skipped = len(rings) - len(hazards)

    items: list[dict] = []
    polygons = 0
    refused: list[str] = []

    for ring in hazards:
        coords = _open_ring(ring.get("coords") or [])
        kind = ring.get("kind", "unknown")
        if len(coords) < 3:
            # prepare() already drops <3, so this is belt-and-braces against a
            # ring that degenerates once the closing vertex is removed.
            refused.append(f"{kind}: only {len(coords)} distinct vertices")
            continue
        if len(coords) > MAX_VERTICES_PER_POLYGON:
            refused.append(
                f"{kind}: {len(coords)} vertices exceeds the "
                f"{MAX_VERTICES_PER_POLYGON}-vertex per-polygon cap")
            continue
        if home is not None:
            zone = {"coords": ring["coords"]}          # closed ring for distance
            if dist_to_zone_m(home, zone) < HOME_CLEARANCE_M:
                raise ValueError(
                    f"home is inside or within {HOME_CLEARANCE_M:.0f} m of a "
                    f"{kind} exclusion -- enabling this fence would breach it "
                    f"immediately. Move home, or resolve the hazard.")
        polygons += 1
        count = len(coords)
        for c in coords:
            items.append({
                "command": FENCE_POLYGON_VERTEX_EXCLUSION,
                "lat": c["lat"],
                "lon": c["lon"],
                # param1 carries the vertex count for EVERY vertex of the
                # polygon, not just the first -- ArduPilot reads it per item.
                "vertex_count": count,
                "kind": kind,
            })

    if refused:
        raise ValueError(
            "cannot build a complete exclusion fence: " + "; ".join(refused)
            + ". Refusing rather than uploading a partial fence.")

    if len(items) > max_points:
        raise ValueError(
            f"exclusion fence needs {len(items)} points but the limit is "
            f"{max_points} ({polygons} hazard polygons). Reduce the planning "
            f"radius or raise MAX_FENCE_POINTS against a real measurement.")

    return {
        "items": items,
        "polygons": polygons,
        "points": len(items),
        # Spray-quality keepouts deliberately not fenced -- see module docstring.
        "skipped": skipped,
        "refused": refused,
    }

"""Unit tests for onboard exclusion-fence construction.

Plain dicts throughout -- no vehicle, no pymavlink, no network. The rings are
built the way keepout_watch.prepare() emits them, so these tests exercise the
real seam between the proximity monitor and the hard fence.
"""

import pytest

from app import onboard_fence
from app.keepout_watch import prepare


def _ring(kind, coords):
    """A zone in the shape gis_zones/keepout_watch use: closed ring."""
    pts = [{"lat": la, "lon": lo} for la, lo in coords]
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    return {"kind": kind, "coords": pts}


# A small square well away from anything, and a second one further east.
POWERLINE = _ring("powerline", [
    (39.9040, -95.8000), (39.9040, -95.7990),
    (39.9050, -95.7990), (39.9050, -95.8000)])
POWERLINE_2 = _ring("powerline", [
    (39.9040, -95.7900), (39.9040, -95.7890),
    (39.9050, -95.7890), (39.9050, -95.7900)])
POND = _ring("water", [
    (39.9060, -95.8000), (39.9060, -95.7990),
    (39.9070, -95.7990), (39.9070, -95.8000)])

FAR_HOME = {"lat": 39.8000, "lon": -95.9000}


def test_hazard_becomes_an_exclusion_polygon():
    out = onboard_fence.build_exclusion_items(prepare([POWERLINE]), home=FAR_HOME)
    assert out["polygons"] == 1
    # Square: 4 distinct vertices once the repeated closing point is dropped.
    assert out["points"] == 4
    assert all(i["command"] == onboard_fence.FENCE_POLYGON_VERTEX_EXCLUSION
               for i in out["items"])
    # Every vertex carries the polygon's own count -- ArduPilot reads it per item.
    assert {i["vertex_count"] for i in out["items"]} == {4}


def test_spray_quality_keepouts_are_not_fenced():
    """A pond is not a hazard. Fencing it would fire FENCE_ACTION on a harmless
    overflight, and could block RTL if it sat between the field and home."""
    out = onboard_fence.build_exclusion_items(prepare([POND]), home=FAR_HOME)
    assert out["polygons"] == 0
    assert out["items"] == []
    assert out["skipped"] == 1


def test_mixed_set_fences_only_the_hazard():
    out = onboard_fence.build_exclusion_items(
        prepare([POWERLINE, POND, POWERLINE_2]), home=FAR_HOME)
    assert out["polygons"] == 2
    assert out["skipped"] == 1
    assert {i["kind"] for i in out["items"]} == {"powerline"}


def test_closing_vertex_is_dropped():
    """gis_zones closes its rings; ArduPilot polygons are implicitly closed, so
    the duplicate would add a zero-length edge and waste a scarce point."""
    assert POWERLINE["coords"][0] == POWERLINE["coords"][-1]
    out = onboard_fence.build_exclusion_items(prepare([POWERLINE]), home=FAR_HOME)
    coords = [(i["lat"], i["lon"]) for i in out["items"]]
    assert len(coords) == len(set(coords)) == 4


def test_home_inside_an_exclusion_is_refused():
    """Home inside the fence means a breach the moment the fence is enabled --
    on most configurations an immediate RTL to a point that is itself breaching."""
    inside = {"lat": 39.9045, "lon": -95.7995}
    with pytest.raises(ValueError, match="home is inside"):
        onboard_fence.build_exclusion_items(prepare([POWERLINE]), home=inside)


def test_home_just_outside_but_within_clearance_is_refused():
    """~15 m north of the boundary: outside the polygon, still too close."""
    near = {"lat": 39.9050 + 15.0 / 111_320.0, "lon": -95.7995}
    with pytest.raises(ValueError, match="within"):
        onboard_fence.build_exclusion_items(prepare([POWERLINE]), home=near)


def test_home_clear_of_the_exclusion_is_accepted():
    clear = {"lat": 39.9050 + 200.0 / 111_320.0, "lon": -95.7995}
    out = onboard_fence.build_exclusion_items(prepare([POWERLINE]), home=clear)
    assert out["polygons"] == 1


def test_no_home_skips_the_clearance_check():
    """Pre-GPS-fix planning still builds a fence; the check runs at upload."""
    out = onboard_fence.build_exclusion_items(prepare([POWERLINE]), home=None)
    assert out["polygons"] == 1


def test_point_budget_overflow_refuses_rather_than_truncates():
    """A fence that silently omits a wire is worse than no fence -- an operator
    trusts the former. keepout_watch learned the same lesson by measurement."""
    many = [POWERLINE, POWERLINE_2]
    with pytest.raises(ValueError, match="needs 8 points but the limit is 4"):
        onboard_fence.build_exclusion_items(
            prepare(many), home=FAR_HOME, max_points=4)


def test_pathological_ring_is_refused_not_simplified():
    """A 4,952-vertex ring was measured at Topeka. One would eat the whole
    budget; simplifying geometry that protects an airframe is not our call."""
    n = onboard_fence.MAX_VERTICES_PER_POLYGON + 5
    coords = [(39.90 + 0.0001 * i, -95.80 + 0.0001 * (i % 7)) for i in range(n)]
    with pytest.raises(ValueError, match="exceeds the"):
        onboard_fence.build_exclusion_items(
            prepare([_ring("powerline", coords)]), home=FAR_HOME)


def test_empty_and_missing_input_are_safe():
    for prepared in ({}, None, {"rings": []}):
        out = onboard_fence.build_exclusion_items(prepared, home=FAR_HOME)
        assert out == {"items": [], "polygons": 0, "points": 0,
                       "skipped": 0, "refused": []}


def test_fence_geometry_matches_the_monitor_exactly():
    """The whole point of building from prepare()'s output: the hard fence and
    the soft proximity monitor cannot describe different ground."""
    prepared = prepare([POWERLINE, POND])
    out = onboard_fence.build_exclusion_items(prepared, home=FAR_HOME)
    monitor_hazard = [r for r in prepared["rings"] if r["hazard"]][0]
    fence_pts = [(i["lat"], i["lon"]) for i in out["items"]]
    monitor_pts = [(c["lat"], c["lon"]) for c in monitor_hazard["coords"][:-1]]
    assert fence_pts == monitor_pts

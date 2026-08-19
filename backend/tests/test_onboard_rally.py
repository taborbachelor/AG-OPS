"""Unit tests for rally-point construction and hazard validation.

Plain dicts throughout -- no vehicle, no pymavlink, no network. Rings are
built the way keepout_watch.prepare() emits them, mirroring
test_onboard_fence.py's setup so the two hazard-avoidance paths (hard fence,
alternate RTL destination) are exercised the same way.
"""

import pytest

from app import onboard_rally
from app.keepout_watch import prepare


def _ring(kind, coords):
    pts = [{"lat": la, "lon": lo} for la, lo in coords]
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    return {"kind": kind, "coords": pts}


# A powerline corridor sitting on a direct line between HOME and FIELD_SIDE.
POWERLINE = _ring("powerline", [
    (39.9040, -95.8000), (39.9040, -95.7990),
    (39.9050, -95.7990), (39.9050, -95.8000)])
POND = _ring("water", [
    (39.9060, -95.8000), (39.9060, -95.7990),
    (39.9070, -95.7990), (39.9070, -95.8000)])

HOME = {"lat": 39.9000, "lon": -95.7995}          # south of the powerline
BEYOND_THE_LINE = {"lat": 39.9100, "lon": -95.7995}  # north of it -- leg crosses
FAR_CLEAR_EAST = {"lat": 39.9045, "lon": -95.7500}   # clear of the ring and the leg


def test_candidate_clear_of_hazards_is_accepted():
    candidate = {"lat": FAR_CLEAR_EAST["lat"], "lon": FAR_CLEAR_EAST["lon"], "alt": 50.0}
    out = onboard_rally.build_rally_items([candidate], prepare([POWERLINE]), home=HOME)
    assert out["points"] == 1
    assert out["items"][0]["command"] == onboard_rally.NAV_RALLY_POINT
    assert out["items"][0]["alt"] == 50.0
    # break_alt/land_dir default sensibly when omitted.
    assert out["items"][0]["break_alt"] == 50.0
    assert out["items"][0]["land_dir"] == 0.0


def test_candidate_inside_the_hazard_clearance_is_refused():
    too_close = {"lat": 39.9045, "lon": -95.7995, "alt": 50.0}  # inside the ring
    with pytest.raises(ValueError, match="clearance"):
        onboard_rally.build_rally_items([too_close], prepare([POWERLINE]), home=HOME)


def test_candidate_clear_but_leg_crosses_a_hazard_is_refused():
    """The point itself is clear, but the straight home<->point leg cuts
    straight through the powerline corridor -- ArduPilot flies rally legs
    straight with no rerouting, so this must be refused too."""
    candidate = {"lat": BEYOND_THE_LINE["lat"], "lon": BEYOND_THE_LINE["lon"], "alt": 50.0}
    with pytest.raises(ValueError, match="home<->rally leg"):
        onboard_rally.build_rally_items([candidate], prepare([POWERLINE]), home=HOME)


def test_no_home_refuses_every_candidate():
    """Unlike the fence (buildable pre-GPS-fix), a rally point's whole
    purpose is the leg from home, so an unknown home refuses outright."""
    candidate = {"lat": FAR_CLEAR_EAST["lat"], "lon": FAR_CLEAR_EAST["lon"], "alt": 50.0}
    with pytest.raises(ValueError, match="home position unknown"):
        onboard_rally.build_rally_items([candidate], prepare([POWERLINE]), home=None)


def test_one_bad_candidate_refuses_the_whole_batch():
    """Never upload a partial rally set that silently dropped an unsafe
    candidate -- same discipline as onboard_fence's refuse-not-truncate."""
    good = {"lat": FAR_CLEAR_EAST["lat"], "lon": FAR_CLEAR_EAST["lon"], "alt": 50.0}
    bad = {"lat": 39.9045, "lon": -95.7995, "alt": 50.0}
    with pytest.raises(ValueError, match="cannot build a verified rally set"):
        onboard_rally.build_rally_items([good, bad], prepare([POWERLINE]), home=HOME)


def test_spray_quality_keepouts_do_not_block_a_rally_point():
    """A pond is not an airframe hazard -- a rally point near one is fine."""
    candidate = {"lat": 39.9065, "lon": -95.7995, "alt": 50.0}  # inside the pond ring
    out = onboard_rally.build_rally_items([candidate], prepare([POND]), home=HOME)
    assert out["points"] == 1


def test_explicit_break_alt_and_land_dir_are_carried_through():
    candidate = {"lat": FAR_CLEAR_EAST["lat"], "lon": FAR_CLEAR_EAST["lon"],
                "alt": 60.0, "break_alt": 40.0, "land_dir": 270.0}
    out = onboard_rally.build_rally_items([candidate], prepare([POWERLINE]), home=HOME)
    item = out["items"][0]
    assert item["alt"] == 60.0
    assert item["break_alt"] == 40.0
    assert item["land_dir"] == 270.0


def test_point_budget_overflow_refuses_rather_than_truncates():
    candidates = [{"lat": 39.9045 + 0.001 * i, "lon": -95.7500, "alt": 50.0}
                 for i in range(3)]
    with pytest.raises(ValueError, match="exceeds the 2-point limit"):
        onboard_rally.build_rally_items(
            candidates, prepare([POWERLINE]), home=HOME, max_points=2)


def test_empty_and_missing_input_are_safe():
    for prepared in ({}, None, {"rings": []}):
        out = onboard_rally.build_rally_items([], prepared, home=HOME)
        assert out == {"items": [], "points": 0, "refused": []}

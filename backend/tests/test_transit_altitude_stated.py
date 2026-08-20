"""The plan payload STATES the transit altitude (TASK-024, LANES seam S7).

`coverage_multi._leg()` built transit points with lat/lon and no `alt`, so the
number existed only as prose in the module docstring and every consumer
invented its own: `MapView3D.jsx` guessed 60 m -- drawing transits 40 m above
20 m spray passes, a climb the aircraft does not fly -- while
`SprayPanel.jsx`'s upload backfill guessed the panel's spray altitude and
happened to be right. One of those two was visible to the operator; neither was
stated.

These pin the STATEMENT, not a new policy. Whether a transit should have its
OWN altitude (a real climb between fields, weighed against airframe and
battery) is seam S7's open decision and belongs to Tabor + AIR. If that
decision later says yes, these tests SHOULD fail -- that is the point of them.
Until then, mission behaviour is unchanged: transit altitude is spray altitude,
now said out loud.
"""
import math
import unittest

from app.coverage_multi import plan_multi

# Geometry deliberately identical to test_hazard_reroute_planning.py's
# two-fields-with-a-power-line-between-them fixture: that one is already proven
# to produce a rerouted transit, and a detour is the case this file most needs.
_M_PER_DEG = math.pi / 180.0 * 6371008.8
LAT, LON = 39.90, -95.80


def _rect(lat0, lon0, w_m, h_m):
    dlat = h_m / _M_PER_DEG
    dlon = w_m / (_M_PER_DEG * math.cos(math.radians(lat0)))
    return [{"lat": lat0, "lon": lon0},
            {"lat": lat0 + dlat, "lon": lon0},
            {"lat": lat0 + dlat, "lon": lon0 + dlon},
            {"lat": lat0, "lon": lon0 + dlon}]


def _line_ns(lat0, lon, span_m):
    """A north-south power line as a corridor ring (out and back)."""
    d = span_m / _M_PER_DEG
    return [{"lat": lat0 - d, "lon": lon},
            {"lat": lat0 + d, "lon": lon},
            {"lat": lat0 - d, "lon": lon}]


WEST = _rect(LAT, LON, 150, 150)
EAST = _rect(LAT, LON + 600 / (_M_PER_DEG * math.cos(math.radians(LAT))), 150, 150)
MID_LON = LON + 350 / (_M_PER_DEG * math.cos(math.radians(LAT)))
LINE = _line_ns(LAT + 75 / _M_PER_DEG, MID_LON, 500)
HOME = {"lat": LAT, "lon": LON}

SPRAY_ALT = 20.0


def _job(**kw):
    kw.setdefault("home", HOME)
    return plan_multi([WEST, EAST], 40.0, kw.pop("alt_m", SPRAY_ALT), **kw)


def _all_transit_points(job):
    return [p for leg in job["transits"] for p in leg["pts"]]


class TestEveryTransitPointStatesIt(unittest.TestCase):
    def test_the_payload_has_transit_legs_to_check(self):
        # Guards the rest of the class: an empty transit list would make every
        # "all points carry alt" assertion below vacuously true.
        job = _job()
        self.assertEqual(len(job["transits"]), 3, "home->first, between, ->home")
        self.assertTrue(_all_transit_points(job))

    def test_no_transit_point_is_missing_an_altitude(self):
        for p in _all_transit_points(_job()):
            self.assertIn("alt", p, f"transit point without an altitude: {p}")
            self.assertIsNotNone(p["alt"])

    def test_transit_altitude_is_the_spray_altitude(self):
        for p in _all_transit_points(_job()):
            self.assertEqual(p["alt"], SPRAY_ALT)

    def test_it_tracks_the_requested_altitude_and_is_not_a_constant(self):
        # A hardcoded literal would pass the test above at alt_m=20 and be a
        # new unstated default at any other altitude.
        for alt in (12.0, 25.0, 60.0):
            job = _job(alt_m=alt)
            for p in _all_transit_points(job):
                self.assertEqual(p["alt"], alt, f"at alt_m={alt}")

    def test_detour_vertices_carry_it_too(self):
        # The vertices a hazard reroute INSERTS are the points most likely to
        # be missed: they are built on a different line from the endpoints.
        job = _job(hazards=[LINE], hazard_buffer_m=25.0)
        detours = [leg for leg in job["transits"] if leg["kind"] == "detour"]
        self.assertTrue(detours, "fixture must produce a detour to be meaningful")
        inserted = [p for leg in detours for p in leg["pts"][1:-1]]
        self.assertTrue(inserted, "a detour leg must carry inserted vertices")
        for p in inserted:
            self.assertEqual(p["alt"], SPRAY_ALT)


class TestTheMissionHasNoAltlessWaypoint(unittest.TestCase):
    """`SprayPanel.jsx` uploads `combined_waypoints` and backfills any missing
    alt (`w.alt != null ? w.alt : alt`). That backfill was load-bearing because
    transit detour vertices reached it with no altitude at all. It stays, as a
    belt, but it must no longer be the only thing supplying the number."""

    def test_every_combined_waypoint_states_its_altitude(self):
        for job in (_job(), _job(hazards=[LINE], hazard_buffer_m=25.0)):
            wps = job["combined_waypoints"]
            self.assertTrue(wps)
            missing = [w for w in wps if w.get("alt") is None]
            self.assertEqual(missing, [], "waypoints uploaded without an altitude")

    def test_the_whole_mission_is_flat_at_spray_altitude(self):
        # Mission behaviour is UNCHANGED by this task: no leg climbs.
        alts = {w["alt"] for w in _job()["combined_waypoints"]}
        self.assertEqual(alts, {SPRAY_ALT})


class TestTheGuessesAreGone(unittest.TestCase):
    """Mutation guards naming the two numbers that used to stand in for the
    stated one. Neither may reappear as a transit default."""

    def test_transit_is_not_drawn_at_the_old_60m_guess(self):
        # MapView3D.jsx:359 was `cart(la, lo, al ?? 60)`.
        for p in _all_transit_points(_job()):
            self.assertNotEqual(p["alt"], 60.0)

    def test_transit_does_not_sit_above_the_spray_passes(self):
        # The visible symptom of the guess: transits floating over the field.
        job = _job()
        field_alts = [w["alt"] for f in job["fields"] for w in f["waypoints"]]
        self.assertTrue(field_alts)
        for p in _all_transit_points(job):
            self.assertLessEqual(p["alt"], max(field_alts))


if __name__ == "__main__":
    unittest.main()

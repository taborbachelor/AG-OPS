"""Connector-leg rerouting at the PLANNER level (coverage + coverage_multi).

`test_reroute.py` proves the geometry. This proves the planner actually uses
it: that a planned mission's connecting legs clear hazard corridors, that the
waypoint/leg bookkeeping stays consistent once detours insert extra points,
and that anything unresolved is reported rather than swallowed.
"""

import math
import unittest
from unittest import mock

from fastapi import HTTPException

from app.coverage import _MAX_CLIP_WORK, plan_coverage
from app.coverage_multi import plan_multi
from app.reroute import hazard_hull, segment_enters_hull
from app.routers import coverage as cov
from app.routers import coverage_multi as cm

_M_PER_DEG = math.pi / 180.0 * 6371008.8


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


def _project_all(pts, lat0, lon0):
    cos_lat = math.cos(math.radians(lat0))
    return [((p["lon"] - lon0) * _M_PER_DEG * cos_lat,
             (p["lat"] - lat0) * _M_PER_DEG) for p in pts]


def _assert_path_clears(case, path_pts, hazard_rings, buffer_m, lat0, lon0):
    """No consecutive pair of waypoints may enter a hazard's cleared hull."""
    hulls = [hazard_hull(_project_all(r, lat0, lon0), buffer_m)
             for r in hazard_rings]
    xy = _project_all(path_pts, lat0, lon0)
    for i in range(1, len(xy)):
        for h in hulls:
            case.assertFalse(
                segment_enters_hull(xy[i - 1], xy[i], h),
                f"leg {i - 1}->{i} still crosses a hazard")


LAT, LON = 39.90, -95.80


class SingleFieldTests(unittest.TestCase):
    """A line straight down the middle of a field: every hop between the
    sub-segments on either side would otherwise cross it."""

    FIELD = _rect(LAT, LON, 400, 300)
    LINE = _line_ns(LAT + 150 / _M_PER_DEG, LON + 200 / (
        _M_PER_DEG * math.cos(math.radians(LAT))), 400)

    def _plan(self, **kw):
        return plan_coverage(self.FIELD, 30.0, 100.0,
                             keepouts=[self.LINE], keepout_buffer_m=20.0,
                             angle_deg=0.0, **kw)

    def test_without_hazards_legs_still_cross(self):
        # Baseline: this is the behavior being fixed. If this ever stops
        # crossing, the fixture no longer exercises the bug.
        plan = self._plan()
        self.assertGreater(plan["stats"]["keepout_overflights"], 0)

    def test_with_hazards_no_leg_crosses(self):
        plan = self._plan(hazards=[self.LINE], hazard_buffer_m=20.0)
        self.assertGreater(plan["stats"]["hazard_reroutes"], 0,
                           "expected at least one leg to be rerouted")
        self.assertEqual(plan["stats"]["hazard_overflights"], 0)
        _assert_path_clears(self, plan["waypoints"], [self.LINE], 20.0,
                            LAT, LON)

    def test_leg_kinds_align_with_waypoints(self):
        plan = self._plan(hazards=[self.LINE], hazard_buffer_m=20.0)
        self.assertEqual(len(plan["leg_kinds"]), len(plan["waypoints"]) - 1)
        self.assertIn("detour", plan["leg_kinds"])
        self.assertIn("spray", plan["leg_kinds"])

    def test_detours_add_waypoints_so_pairing_is_no_longer_valid(self):
        """The reason leg_kinds exists.

        Callers used to infer spray-vs-hop from index parity (waypoints in
        strict pairs). With detours inserted that assumption breaks, and a UI
        still using it would draw hop legs as SPRAY legs — i.e. show spraying
        over a keepout. Pin the fact that parity is genuinely broken here.
        """
        plan = self._plan(hazards=[self.LINE], hazard_buffer_m=20.0)
        parity_says_spray = [i for i in range(0, len(plan["leg_kinds"]), 2)]
        actual_spray = [i for i, k in enumerate(plan["leg_kinds"])
                        if k == "spray"]
        self.assertNotEqual(parity_says_spray, actual_spray)

    def test_hazard_free_plan_is_unchanged(self):
        """No hazards => byte-for-byte the old plan (plus leg_kinds)."""
        before = self._plan()
        after = self._plan(hazards=[], hazard_buffer_m=20.0)
        self.assertEqual(before["waypoints"], after["waypoints"])

    def test_stats_absent_without_hazards(self):
        plan = self._plan()
        self.assertNotIn("hazard_reroutes", plan["stats"])

    def test_path_gets_longer_not_shorter(self):
        # A detour is extra distance by definition; a "reroute" that shortened
        # the path would mean we dropped part of the pattern.
        base = self._plan()
        routed = self._plan(hazards=[self.LINE], hazard_buffer_m=20.0)
        self.assertGreater(routed["stats"]["path_length_m"],
                           base["stats"]["path_length_m"])

    def test_ordering_collapses_repeated_crossings(self):
        """A field bisected by a line must be flown one side at a time.

        Rerouting every alternating hop is correct but produced a mission 2.6x
        longer than the straight plan with 5x the waypoints. Ordering the
        sub-segments so same-side passes fly together costs ONE crossing.
        """
        plan = self._plan(hazards=[self.LINE], hazard_buffer_m=20.0)
        base = self._plan()
        self.assertEqual(plan["stats"]["hazard_reroutes"], 1,
                         "should cross the line exactly once")
        ratio = (plan["stats"]["path_length_m"]
                 / base["stats"]["path_length_m"])
        self.assertLess(ratio, 1.5,
                        f"rerouted plan is {ratio:.2f}x the straight plan — "
                        "segment ordering has regressed")
        _assert_path_clears(self, plan["waypoints"], [self.LINE], 20.0,
                            LAT, LON)

    def test_ordering_covers_every_segment_exactly_once(self):
        """Reordering must not drop or duplicate sprayed ground."""
        base = self._plan()
        routed = self._plan(hazards=[self.LINE], hazard_buffer_m=20.0)
        spray_len = lambda p: sum(  # noqa: E731
            math.dist((p["waypoints"][i]["lat"], p["waypoints"][i]["lon"]),
                      (p["waypoints"][i + 1]["lat"], p["waypoints"][i + 1]["lon"]))
            for i, k in enumerate(p["leg_kinds"]) if k == "spray")
        self.assertAlmostEqual(spray_len(base), spray_len(routed), places=6,
                               msg="sprayed length changed when reordering")

    def test_bad_hazard_geometry_rejected(self):
        with self.assertRaises(ValueError):
            self._plan(hazards=[[{"lat": 39.9, "lon": -95.8}]])
        with self.assertRaises(ValueError):
            self._plan(hazards=[self.LINE], hazard_buffer_m=float("nan"))


class MultiFieldTests(unittest.TestCase):
    """Two fields with a power line running between them — the transit leg
    from one to the other crosses it. Transits were not even CHECKED before."""

    WEST = _rect(LAT, LON, 150, 150)
    EAST = _rect(LAT, LON + 600 / (_M_PER_DEG * math.cos(math.radians(LAT))),
                 150, 150)
    MID_LON = LON + 350 / (_M_PER_DEG * math.cos(math.radians(LAT)))
    LINE = _line_ns(LAT + 75 / _M_PER_DEG, MID_LON, 500)
    HOME = {"lat": LAT, "lon": LON}

    def _job(self, **kw):
        return plan_multi([self.WEST, self.EAST], 40.0, 100.0,
                          home=self.HOME, **kw)

    def test_transit_is_rerouted_around_the_line(self):
        job = self._job(hazards=[self.LINE], hazard_buffer_m=25.0)
        kinds = [t["kind"] for t in job["transits"]]
        self.assertIn("detour", kinds, "the crossing transit must detour")
        self.assertGreater(job["totals"]["hazard_reroutes"], 0)

    def test_combined_mission_clears_the_hazard(self):
        job = self._job(hazards=[self.LINE], hazard_buffer_m=25.0)
        self.assertEqual(job["totals"]["hazard_overflights"], 0)
        _assert_path_clears(self, job["combined_waypoints"], [self.LINE],
                            25.0, LAT, LON)

    def test_detour_points_enter_the_mission(self):
        plain = self._job()
        routed = self._job(hazards=[self.LINE], hazard_buffer_m=25.0)
        self.assertGreater(len(routed["combined_waypoints"]),
                           len(plain["combined_waypoints"]))

    def test_combined_leg_kinds_align(self):
        job = self._job(hazards=[self.LINE], hazard_buffer_m=25.0)
        self.assertEqual(len(job["combined_leg_kinds"]),
                         len(job["combined_waypoints"]) - 1)

    def test_home_leg_hazard_is_reported_not_routed(self):
        """RTL flies straight home and ignores our keepouts.

        So a home leg crossing a hazard must be REPORTED, and its detour must
        NOT be appended to the mission — flying out to a detour vertex and
        then RTL-ing straight back across the line is worse than not trying.
        """
        # Home sits west of the line; the last field is east of it.
        job = self._job(hazards=[self.LINE], hazard_buffer_m=25.0)
        home_leg = job["transits"][-1]
        self.assertTrue(job["totals"]["home_leg_hazard"],
                        "a home leg crossing the line must be flagged")
        # None of the home leg's interior points may have leaked into the
        # mission (the mission ends at the final field waypoint).
        for pt in home_leg["pts"][1:-1]:
            self.assertNotIn({"lat": pt["lat"], "lon": pt["lon"],
                              "alt": 100.0}, job["combined_waypoints"])

    def test_no_hazards_leaves_transits_direct(self):
        job = self._job()
        self.assertTrue(all(t["kind"] == "direct" for t in job["transits"]))
        self.assertEqual(job["totals"]["hazard_overflights"], 0)
        self.assertFalse(job["totals"]["home_leg_hazard"])

    def test_reversal_keeps_leg_kinds_aligned(self):
        # A field flown from its far end reverses both waypoints and kinds;
        # a misalignment here would mislabel spray legs as hops.
        job = self._job(hazards=[self.LINE], hazard_buffer_m=25.0)
        self.assertEqual(len(job["combined_leg_kinds"]),
                         len(job["combined_waypoints"]) - 1)
        self.assertIn("spray", job["combined_leg_kinds"])


class FailClosedTests(unittest.TestCase):
    """An unresolved hazard crossing is a path THROUGH the conductor.

    Measured against a live 115 kV Evergy line near Topeka: before this gate,
    plan_auto happily returned a plan whose closest approach to the line was
    1.7 m. Warning about that is not enough — the operator has to accept it
    deliberately, the same posture the zone-service outage already takes.
    """

    FIELD = _rect(LAT, LON, 400, 300)
    # A line running far past the field, so no detour is short enough.
    LONG_LINE = _line_ns(LAT + 150 / _M_PER_DEG,
                         LON + 200 / (_M_PER_DEG * math.cos(math.radians(LAT))),
                         6000)

    def _zones(self):
        ring = self.LONG_LINE + [self.LONG_LINE[0]]
        return {"water": [], "trees": [], "buildings": [],
                "powerline": [{"kind": "powerline", "coords": ring,
                               "tags": {"power": "line"}}]}

    def _plan(self, **kw):
        req = cov.AutoCoverageRequest(polygon=self.FIELD, swath=30.0,
                                      powerline_buffer=25.0, **kw)
        with mock.patch.object(cov, "fetch_zones", return_value=self._zones()):
            return cov.plan_auto(req)

    def test_refuses_when_a_leg_still_crosses(self):
        with self.assertRaises(HTTPException) as ctx:
            self._plan()
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["error"], "hazard_crossings")
        self.assertGreater(ctx.exception.detail["count"], 0)

    def test_explicit_opt_in_returns_the_plan(self):
        plan = self._plan(allow_hazard_crossings=True)
        self.assertGreater(plan["stats"]["hazard_overflights"], 0)
        self.assertTrue(plan["waypoints"])

    def test_no_crossing_needs_no_opt_in(self):
        """A field clear of the line plans normally — the gate is not a
        blanket refusal whenever a powerline exists in the area."""
        far = _rect(LAT + 0.02, LON - 0.02, 200, 200)
        req = cov.AutoCoverageRequest(polygon=far, swath=30.0,
                                      powerline_buffer=25.0)
        with mock.patch.object(cov, "fetch_zones", return_value=self._zones()):
            plan = cov.plan_auto(req)
        self.assertEqual(plan["stats"]["hazard_overflights"], 0)


class BudgetTests(unittest.TestCase):
    """Hazard ordering shares ONE CPU allowance with clipping, across a whole
    multi-field job. An early version tested every candidate segment at every
    step and burned ~26% of the job budget on a single 200-segment field —
    enough to fail a 4-field job closed. Ranking by distance and stopping at
    the first clear candidate brought it to ~1%."""

    def test_large_field_stays_well_inside_the_budget(self):
        field = _rect(LAT, LON, 2000, 1000)
        line = _line_ns(LAT + 500 / _M_PER_DEG,
                        LON + 1000 / (_M_PER_DEG * math.cos(math.radians(LAT))),
                        2000)
        budget = [_MAX_CLIP_WORK]
        plan = plan_coverage(field, 10.0, 100.0, keepouts=[line],
                             keepout_buffer_m=20.0, angle_deg=0.0,
                             work_budget=budget, hazards=[line],
                             hazard_buffer_m=20.0)
        used = _MAX_CLIP_WORK - budget[0]
        self.assertGreater(plan["stats"]["n_segments"], 100)
        self.assertLess(used, _MAX_CLIP_WORK * 0.10,
                        f"hazard planning used {used} of {_MAX_CLIP_WORK} "
                        "budget on ONE field — a multi-field job would fail")
        self.assertEqual(plan["stats"]["hazard_overflights"], 0)


class RouterWiringTests(unittest.TestCase):
    """The library work is useless if the API never passes hazards down.

    Powerline zones must reach plan_coverage/plan_multi as `hazards`, and the
    other zone kinds must NOT — rerouting around every treeline would add
    flight time for no safety gain.
    """

    FIELD = _rect(LAT, LON, 300, 200)

    def _zones(self, kind, ring):
        base = {"water": [], "trees": [], "buildings": [], "powerline": []}
        base[kind] = [{"kind": kind, "coords": ring + [ring[0]], "tags": {}}]
        return base

    def _capture_auto(self, zones):
        seen = {}

        def fake_plan(poly, **kw):
            seen.update(kw)
            return {"waypoints": [], "leg_kinds": [], "stats": {}}

        req = cov.AutoCoverageRequest(polygon=self.FIELD)
        with mock.patch.object(cov, "fetch_zones", return_value=zones),                 mock.patch.object(cov, "plan_coverage", side_effect=fake_plan):
            cov.plan_auto(req)
        return seen

    def test_powerline_zone_becomes_a_hazard(self):
        line = _line_ns(LAT + 100 / _M_PER_DEG,
                        LON + 150 / (_M_PER_DEG * math.cos(math.radians(LAT))),
                        300)
        seen = self._capture_auto(self._zones("powerline", line))
        self.assertIsNotNone(seen.get("hazards"))
        self.assertEqual(len(seen["hazards"]), 1)
        self.assertEqual(seen["hazard_buffer_m"], 20.0)

    def test_water_zone_is_not_a_hazard(self):
        pond = [{"lat": LAT + 0.0005, "lon": LON + 0.0005},
                {"lat": LAT + 0.0006, "lon": LON + 0.0005},
                {"lat": LAT + 0.0006, "lon": LON + 0.0006},
                {"lat": LAT + 0.0005, "lon": LON + 0.0006}]
        seen = self._capture_auto(self._zones("water", pond))
        self.assertIsNone(seen.get("hazards"),
                          "spray-quality keepouts must not force detours")

    def test_multi_router_passes_hazards(self):
        seen = {}

        def fake_multi(fields, swath, alt, **kw):
            seen.update(kw)
            return {"fields": [], "transits": [], "totals": {},
                    "flight_order": [], "combined_waypoints": [],
                    "combined_leg_kinds": []}

        line = _line_ns(LAT + 100 / _M_PER_DEG,
                        LON + 150 / (_M_PER_DEG * math.cos(math.radians(LAT))),
                        300)
        req = cm.MultiRequest(fields=[self.FIELD])
        with mock.patch.object(cm, "fetch_zones",
                               return_value=self._zones("powerline", line)),                 mock.patch.object(cm, "plan_multi", side_effect=fake_multi):
            cm.plan_multi_endpoint(req)
        self.assertIsNotNone(seen.get("hazards"))
        self.assertEqual(seen["hazard_buffer_m"], 20)


if __name__ == "__main__":
    unittest.main()

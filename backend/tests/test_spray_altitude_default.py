"""The spray-plan altitude default (TASK-012, LANES seam S3).

`alt` defaulted to 100 m, which is not a spray altitude under any reading of
the frame -- real aerial application flies 10-25 m AGL. AIR settled the
semantic half of S3 (a spray-plan `alt` means metres AGL); the number was
PLANNER's to set.

These pin the number, that every request model actually uses it, and the one
cross-lane relationship it participates in: seam S2's bank agreement assumes a
spray pass sits inside guardian's low-altitude band, which was false while the
default was 100 m.
"""
import unittest

from app.coverage import DEFAULT_MAX_BANK_DEG, DEFAULT_SPRAY_ALT_M, plan_coverage
from app.guardian import GuardianConfig
from app.routers.coverage import AutoCoverageRequest, CoverageRequest
from app.routers.coverage_multi import MultiRequest

# A small rectangular field, in lat/lon.
FIELD = [{"lat": 39.900, "lon": -95.800},
         {"lat": 39.900, "lon": -95.795},
         {"lat": 39.904, "lon": -95.795},
         {"lat": 39.904, "lon": -95.800}]


class TestTheNumber(unittest.TestCase):
    def test_default_is_a_real_spray_altitude(self):
        # The band aerial application actually works in. Outside it the number
        # is either useless for spray (too high, drift) or unflyable (too low).
        self.assertGreaterEqual(DEFAULT_SPRAY_ALT_M, 10.0)
        self.assertLessEqual(DEFAULT_SPRAY_ALT_M, 25.0)

    def test_the_placeholder_is_gone(self):
        self.assertNotEqual(DEFAULT_SPRAY_ALT_M, 100.0)


class TestEveryRequestModelUsesIt(unittest.TestCase):
    """Three request models carried their own copy of the placeholder. A
    default that is right in one of them and stale in the other two is the
    same bug wearing a different hat."""

    def test_single_field_plan(self):
        self.assertEqual(CoverageRequest(polygon=FIELD).alt, DEFAULT_SPRAY_ALT_M)

    def test_auto_keepout_plan(self):
        self.assertEqual(AutoCoverageRequest(polygon=FIELD).alt, DEFAULT_SPRAY_ALT_M)

    def test_multi_field_job(self):
        self.assertEqual(MultiRequest(fields=[FIELD]).alt, DEFAULT_SPRAY_ALT_M)

    def test_the_ceiling_did_not_move(self):
        # Only the DEFAULT is a spray altitude. A ferry or scouting plan may
        # legitimately fly high, so the accepted range must not narrow.
        self.assertEqual(CoverageRequest(polygon=FIELD, alt=500).alt, 500)
        with self.assertRaises(Exception):
            CoverageRequest(polygon=FIELD, alt=501)


class TestItReachesTheWaypoints(unittest.TestCase):
    def test_planning_at_the_default_puts_every_waypoint_there(self):
        # The default is worth nothing if it stops at the request model.
        req = CoverageRequest(polygon=FIELD)
        plan = plan_coverage([p.model_dump() for p in req.polygon],
                             swath_m=req.swath, alt_m=req.alt)
        self.assertTrue(plan["waypoints"])
        self.assertTrue(all(w["alt"] == DEFAULT_SPRAY_ALT_M
                            for w in plan["waypoints"]))


class TestSeamS2PremiseHolds(unittest.TestCase):
    """Seam S2 agreed the planner may command 25 deg of bank because guardian
    warns at 31.5 deg BELOW 30 m, and "a spray pass is entirely below
    bank_low_alt_m". At the old 100 m default that premise was simply false --
    the two lanes had agreed on a constraint nobody was flying. This pins the
    relationship so changing either number trips a test instead of quietly
    invalidating the other lane's reasoning."""

    def test_a_default_spray_pass_is_inside_guardians_low_altitude_band(self):
        cfg = GuardianConfig()
        self.assertLess(DEFAULT_SPRAY_ALT_M, cfg.bank_low_alt_m)

    def test_the_planner_ceiling_still_fits_under_the_tightened_warning(self):
        cfg = GuardianConfig()
        low_alt_limit = cfg.bank_warn_deg * cfg.bank_low_alt_factor
        self.assertLess(DEFAULT_MAX_BANK_DEG, low_alt_limit)


if __name__ == "__main__":
    unittest.main()

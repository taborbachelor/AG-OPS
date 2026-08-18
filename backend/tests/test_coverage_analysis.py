"""Coverage analysis — did the passes actually cover the field?

Pass counts and path length say what was PLANNED. These stats say what the
plan achieves on the ground, which is the question an operator has. Keepout
area is excluded from the denominator: not spraying a pond is the plan
working, not a gap.
"""

import math
import unittest

from app.coverage import plan_coverage

_M_PER_DEG = math.pi / 180.0 * 6371008.8
LAT, LON = 39.90, -95.80


def _rect(lat0, lon0, w_m, h_m):
    dlat = h_m / _M_PER_DEG
    dlon = w_m / (_M_PER_DEG * math.cos(math.radians(lat0)))
    return [{"lat": lat0, "lon": lon0},
            {"lat": lat0 + dlat, "lon": lon0},
            {"lat": lat0 + dlat, "lon": lon0 + dlon},
            {"lat": lat0, "lon": lon0 + dlon}]


class CoverageStatsTests(unittest.TestCase):
    FIELD = _rect(LAT, LON, 400, 300)

    def test_legacy_call_gets_no_coverage_keys(self):
        """The no-keepout response is a pinned contract (TestLegacyRegression).
        Coverage is a diagnostic and must not silently change that shape."""
        st = plan_coverage(self.FIELD, 20.0, 100.0, angle_deg=0.0)["stats"]
        self.assertNotIn("coverage_pct", st)

    def test_clean_field_is_fully_covered(self):
        st = plan_coverage(self.FIELD, 20.0, 100.0, angle_deg=0.0,
                           keepouts=[])["stats"]
        self.assertGreaterEqual(st["coverage_pct"], 99.0)
        self.assertLessEqual(st["uncovered_acres"], 0.05)

    def test_sprayable_area_tracks_the_field(self):
        st = plan_coverage(self.FIELD, 20.0, 100.0, angle_deg=0.0,
                           keepouts=[])["stats"]
        # Sampling is a grid approximation, so allow a few percent.
        self.assertAlmostEqual(st["sprayable_acres"], st["area_acres"],
                               delta=st["area_acres"] * 0.05)

    def test_keepout_area_is_excluded_not_counted_as_a_miss(self):
        """A pond must shrink the DENOMINATOR, not show up as uncovered."""
        d = 60.0 / _M_PER_DEG
        dl = 60.0 / (_M_PER_DEG * math.cos(math.radians(LAT)))
        clat, clon = LAT + 150 / _M_PER_DEG, LON + 200 / (
            _M_PER_DEG * math.cos(math.radians(LAT)))
        pond = [{"lat": clat - d, "lon": clon - dl},
                {"lat": clat + d, "lon": clon - dl},
                {"lat": clat + d, "lon": clon + dl},
                {"lat": clat - d, "lon": clon + dl}]
        st = plan_coverage(self.FIELD, 20.0, 100.0, angle_deg=0.0,
                           keepouts=[pond], keepout_buffer_m=15.0)["stats"]
        self.assertLess(st["sprayable_acres"], st["area_acres"],
                        "the pond should reduce sprayable ground")
        self.assertGreater(st["coverage_pct"], 90.0,
                           "excluded keepout must not read as a coverage miss")

    def test_wider_swath_than_field_still_reports(self):
        tiny = _rect(LAT, LON, 40, 30)
        st = plan_coverage(tiny, 25.0, 100.0, angle_deg=0.0,
                           keepouts=[])["stats"]
        self.assertIn("coverage_pct", st)

    def test_huge_field_coarsens_instead_of_exploding(self):
        """A big field must still answer, and quickly — the sampler coarsens
        rather than refusing or burning the whole CPU budget."""
        big = _rect(LAT, LON, 4000, 3000)
        import time
        t = time.time()
        st = plan_coverage(big, 20.0, 100.0, angle_deg=0.0,
                           keepouts=[])["stats"]
        self.assertIn("coverage_pct", st)
        self.assertLess(time.time() - t, 10.0)


if __name__ == "__main__":
    unittest.main()

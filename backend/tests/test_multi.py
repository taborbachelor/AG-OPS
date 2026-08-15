"""Multi-field job planning + area field detection (no network)."""

import unittest
from unittest import mock

from app.coverage_multi import plan_multi, _dist_m
from app import field_boundaries


def rect(lat0, lon0, w_m=200.0, h_m=100.0):
    """Axis-aligned rectangle with SW corner at (lat0, lon0)."""
    import math
    dlat = h_m / 111320.0
    dlon = w_m / (111320.0 * math.cos(math.radians(lat0)))
    return [
        {"lat": lat0, "lon": lon0},
        {"lat": lat0 + dlat, "lon": lon0},
        {"lat": lat0 + dlat, "lon": lon0 + dlon},
        {"lat": lat0, "lon": lon0 + dlon},
    ]


HOME = {"lat": 39.9000, "lon": -95.8000}
NEAR = rect(39.9010, -95.8000)            # ~110 m north of home
FAR = rect(39.9100, -95.8000)             # ~1.1 km north


class TestPlanMulti(unittest.TestCase):
    def test_orders_near_field_first(self):
        job = plan_multi([FAR, NEAR], 20, 100, home=HOME)
        order = [s["index"] for s in job["flight_order"]]
        self.assertEqual(order, [1, 0], "nearest field (index 1) should fly first")

    def test_transit_legs_home_roundtrip(self):
        job = plan_multi([FAR, NEAR], 20, 100, home=HOME)
        # home->first, between fields, last->home
        self.assertEqual(len(job["transits"]), 3)
        self.assertEqual(job["transits"][0]["from"], "home")
        self.assertGreater(job["totals"]["transit_m"], 0)
        # combined waypoints = both fields' waypoints
        n = sum(len(f["waypoints"]) for f in job["fields"])
        self.assertEqual(len(job["combined_waypoints"]), n)
        self.assertEqual(job["totals"]["fields"], 2)

    def test_reversal_when_far_end_is_closer(self):
        single = plan_multi([NEAR], 20, 100)  # no home: establish waypoints
        wps = single["fields"][0]["waypoints"]
        # Put home on top of the pattern's LAST waypoint: entering reversed is
        # obviously shorter.
        home = {"lat": wps[-1]["lat"], "lon": wps[-1]["lon"]}
        job = plan_multi([NEAR], 20, 100, home=home)
        self.assertTrue(job["flight_order"][0]["reversed"])
        self.assertLess(_dist_m(home, job["combined_waypoints"][0]), 1.0)

    def test_bad_field_skipped_not_fatal(self):
        collinear = [{"lat": 39.9, "lon": -95.8},
                     {"lat": 39.901, "lon": -95.8},
                     {"lat": 39.902, "lon": -95.8}]
        job = plan_multi([NEAR, collinear], 20, 100, home=HOME)
        self.assertEqual(job["totals"]["fields"], 1)
        self.assertEqual(len(job["skipped"]), 1)
        self.assertEqual(job["skipped"][0]["index"], 1)

    def test_all_bad_raises(self):
        collinear = [{"lat": 39.9, "lon": -95.8},
                     {"lat": 39.901, "lon": -95.8},
                     {"lat": 39.902, "lon": -95.8}]
        with self.assertRaises(ValueError):
            plan_multi([collinear], 20, 100)

    def test_totals_are_sums(self):
        job = plan_multi([NEAR, FAR], 20, 100, home=HOME)
        spray = sum(f["stats"]["path_length_m"] for f in job["fields"])
        self.assertAlmostEqual(job["totals"]["spray_path_m"], spray, places=0)
        self.assertAlmostEqual(
            job["totals"]["total_m"],
            job["totals"]["spray_path_m"] + job["totals"]["transit_m"], places=0)


class TestUserKeepouts(unittest.TestCase):
    def test_holes_apply_even_when_zone_service_down(self):
        """Detected in-field holes must clip passes even if OSM is down."""
        from app.routers import coverage_multi as cm

        # Pond centered INSIDE the field (NEAR spans lon -95.8000..-95.79766,
        # lat 39.9010..39.9019) so crossing passes must SPLIT, not shorten.
        pond = [{"lat": 39.90135, "lon": -95.79900},
                {"lat": 39.90165, "lon": -95.79900},
                {"lat": 39.90165, "lon": -95.79866},
                {"lat": 39.90135, "lon": -95.79866}]
        # Fail-closed default: zone-service failure refuses the plan…
        req = cm.MultiRequest(fields=[NEAR], keepouts=[pond])
        with mock.patch.object(cm, "fetch_zones", side_effect=RuntimeError("down")):
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as ctx:
                cm.plan_multi_endpoint(req)
            self.assertEqual(ctx.exception.status_code, 502)
            self.assertEqual(ctx.exception.detail["error"], "zones_unavailable")
            # …and the explicit opt-in still applies the detected holes.
            req = cm.MultiRequest(fields=[NEAR], keepouts=[pond],
                                  allow_missing_zones=True)
            out = cm.plan_multi_endpoint(req)
        self.assertTrue(out["zones_unavailable"])
        f = out["fields"][0]
        # The pond splits at least one pass -> more segments than passes.
        self.assertGreater(f["stats"]["n_segments"], f["stats"]["n_passes"])


class TestFieldsInArea(unittest.TestCase):
    def _parcel(self, lat, lon):
        return {"coords": [
            {"lat": lat, "lon": lon},
            {"lat": lat + 0.001, "lon": lon},
            {"lat": lat + 0.001, "lon": lon + 0.001},
            {"lat": lat, "lon": lon + 0.001},
            {"lat": lat, "lon": lon},
        ], "tags": {"landuse": "farmland"}}

    def test_filters_to_selection(self):
        inside = self._parcel(39.9005, -95.7995)   # centroid inside selection
        outside = self._parcel(39.9500, -95.7000)  # far away
        selection = rect(39.8990, -95.8010, w_m=400, h_m=400)
        with mock.patch.object(field_boundaries, "fetch_fields",
                               return_value=[inside, outside]):
            got, truncated = field_boundaries.fields_in_area(selection)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0], inside)
        self.assertFalse(truncated)

    def test_selection_too_small_raises(self):
        with self.assertRaises(ValueError):
            field_boundaries.fields_in_area([{"lat": 1, "lon": 1}, {"lat": 2, "lon": 2}])


if __name__ == "__main__":
    unittest.main()

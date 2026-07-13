"""Unit tests for the spray-coverage planner (app.coverage).

Test polygons are authored in local ENU meters around a Kansas reference
point, converted to lat/lon with the same equirectangular math the planner
uses, and planner output is converted back to meters for geometric checks.
That keeps all assertions in intuitive metric units.
"""

import math
import unittest

from app.coverage import EARTH_RADIUS_M, plan_coverage

LAT0, LON0 = 39.9042, -95.7997
_M_PER_DEG = math.pi / 180.0 * EARTH_RADIUS_M
_COS_LAT = math.cos(math.radians(LAT0))


def ll_from_xy(x: float, y: float) -> dict:
    """Local meters (east, north) around the reference -> {lat, lon}."""
    return {
        "lat": LAT0 + y / _M_PER_DEG,
        "lon": LON0 + x / (_M_PER_DEG * _COS_LAT),
    }


def xy_from_ll(lat: float, lon: float) -> tuple[float, float]:
    """Inverse of ll_from_xy, for checking planner output in meters."""
    return (lon - LON0) * _M_PER_DEG * _COS_LAT, (lat - LAT0) * _M_PER_DEG


def poly_ll(xy_vertices: list[tuple[float, float]]) -> list[dict]:
    return [ll_from_xy(x, y) for x, y in xy_vertices]


def point_in_polygon_grown(px: float, py: float,
                           poly_xy: list[tuple[float, float]],
                           tol: float = 1.0) -> bool:
    """True if (px, py) is inside the polygon or within tol meters of its
    boundary — pass endpoints sit exactly on the boundary, where plain
    ray-casting is numerically unreliable."""
    n = len(poly_xy)
    # Distance to boundary first: covers on-edge points regardless of parity.
    for i in range(n):
        x1, y1 = poly_xy[i]
        x2, y2 = poly_xy[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        seg_len2 = dx * dx + dy * dy
        t = 0.0 if seg_len2 == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / seg_len2))
        if math.hypot(px - (x1 + t * dx), py - (y1 + t * dy)) <= tol:
            return True
    # Standard even-odd ray cast for strictly interior points.
    inside = False
    for i in range(n):
        x1, y1 = poly_xy[i]
        x2, y2 = poly_xy[(i + 1) % n]
        if (y1 <= py < y2) or (y2 <= py < y1):
            if px < x1 + (py - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
    return inside


def waypoints_xy(plan: dict) -> list[tuple[float, float]]:
    return [xy_from_ll(w["lat"], w["lon"]) for w in plan["waypoints"]]


# 400 m x 200 m axis-aligned field, long edge east-west.
RECT_XY = [(0.0, 0.0), (400.0, 0.0), (400.0, 200.0), (0.0, 200.0)]


class TestRectangle(unittest.TestCase):
    def setUp(self):
        self.plan = plan_coverage(poly_ll(RECT_XY), swath_m=20.0, alt_m=100.0)

    def test_pass_count(self):
        # 200 m extent / 20 m swath with a half-swath inset -> exactly 10 passes.
        self.assertEqual(self.plan["stats"]["n_passes"], 10)
        self.assertEqual(len(self.plan["waypoints"]), 20)

    def test_waypoints_inside_grown_polygon(self):
        for px, py in waypoints_xy(self.plan):
            self.assertTrue(point_in_polygon_grown(px, py, RECT_XY),
                            f"waypoint ({px:.2f}, {py:.2f}) outside polygon+1m")

    def test_serpentine_alternation(self):
        wps = waypoints_xy(self.plan)
        dirs = [(wps[2 * i + 1][0] - wps[2 * i][0],
                 wps[2 * i + 1][1] - wps[2 * i][1]) for i in range(len(wps) // 2)]
        for a, b in zip(dirs, dirs[1:]):
            self.assertLess(a[0] * b[0] + a[1] * b[1], 0.0,
                            "consecutive passes must fly opposite directions")

    def test_path_length(self):
        expected = 10 * 400 + 9 * 20  # passes + serpentine hops
        self.assertAlmostEqual(self.plan["stats"]["path_length_m"], expected,
                               delta=0.05 * expected)

    def test_area_acres(self):
        # 80,000 m^2 = 19.768 acres.
        self.assertAlmostEqual(self.plan["stats"]["area_acres"], 19.768,
                               delta=0.01 * 19.768)

    def test_est_time_uses_speed(self):
        s = self.plan["stats"]
        self.assertAlmostEqual(s["est_time_s"], s["path_length_m"] / 18.0, places=6)

    def test_waypoint_altitude(self):
        self.assertTrue(all(w["alt"] == 100.0 for w in self.plan["waypoints"]))


class TestRotatedRectangle(unittest.TestCase):
    def test_auto_angle_matches_longest_edge(self):
        # Same rectangle rotated 30 deg CCW; with angle_deg=None the planner
        # must recover the long-edge orientation from the geometry alone.
        c, s = math.cos(math.radians(30.0)), math.sin(math.radians(30.0))
        rot_xy = [(x * c - y * s, x * s + y * c) for x, y in RECT_XY]
        plan = plan_coverage(poly_ll(rot_xy), swath_m=20.0, alt_m=100.0)
        diff = abs(plan["stats"]["angle_deg"] - 30.0) % 180.0
        diff = min(diff, 180.0 - diff)
        self.assertLessEqual(diff, 2.0,
                             f"auto angle {plan['stats']['angle_deg']:.2f} not ~30 deg")
        # Geometry is unchanged by rotation, so the pass count must hold too.
        self.assertEqual(plan["stats"]["n_passes"], 10)


class TestConcavePolygons(unittest.TestCase):
    # L-shape: 300 m square with the top-right 180 x 180 corner removed,
    # leaving a bottom arm (x > 120, y < 120) and an upper arm (y > 120).
    L_XY = [(0.0, 0.0), (300.0, 0.0), (300.0, 120.0),
            (120.0, 120.0), (120.0, 300.0), (0.0, 300.0)]

    def test_l_shape_waypoints_inside_and_both_arms(self):
        plan = plan_coverage(poly_ll(self.L_XY), swath_m=20.0, alt_m=80.0,
                             angle_deg=0.0)
        wps = waypoints_xy(plan)
        self.assertGreater(len(wps), 0)
        for px, py in wps:
            self.assertTrue(point_in_polygon_grown(px, py, self.L_XY),
                            f"waypoint ({px:.2f}, {py:.2f}) outside L+1m")
        # Passes must reach deep into each arm, not just the shared corner.
        self.assertTrue(any(px > 250.0 and py < 120.0 for px, py in wps),
                        "no pass in the bottom (east) arm")
        self.assertTrue(any(py > 250.0 for px, py in wps),
                        "no pass in the upper (north) arm")

    def test_u_shape_splits_lines_into_two_segments(self):
        # U-shape: sweep lines above the notch floor (y > 100) must each split
        # into two in-polygon segments, one per prong.
        u_xy = [(0.0, 0.0), (300.0, 0.0), (300.0, 300.0), (200.0, 300.0),
                (200.0, 100.0), (100.0, 100.0), (100.0, 300.0), (0.0, 300.0)]
        plan = plan_coverage(poly_ll(u_xy), swath_m=30.0, alt_m=80.0,
                             angle_deg=0.0)
        wps = waypoints_xy(plan)
        # Lines at y = 15, 45, 75 cross the base once; lines at 105..285
        # (7 of them) cross both prongs -> 3 + 2*7 = 17 passes.
        self.assertEqual(plan["stats"]["n_passes"], 17)
        for px, py in wps:
            self.assertTrue(point_in_polygon_grown(px, py, u_xy),
                            f"waypoint ({px:.2f}, {py:.2f}) outside U+1m")
        self.assertTrue(any(px < 101.0 and py > 200.0 for px, py in wps),
                        "left prong not covered")
        self.assertTrue(any(px > 199.0 and py > 200.0 for px, py in wps),
                        "right prong not covered")


class TestPartialBandHeights(unittest.TestCase):
    """Heights whose extent mod swath falls in (0, swath/2].

    These used to lose their top pass entirely (an unsprayed strip up to
    swath/2 wide along the far edge); the centered line grid must instead
    add a pass and split the overhang between both edges.
    """

    SWATH = 20.0

    def _pass_line_ys(self, height: float) -> list[float]:
        rect = [(0.0, 0.0), (400.0, 0.0), (400.0, height), (0.0, height)]
        plan = plan_coverage(poly_ll(rect), swath_m=self.SWATH, alt_m=100.0,
                             angle_deg=0.0)
        return sorted({y for _, y in waypoints_xy(plan)})

    def assert_full_coverage(self, ys: list[float], height: float):
        """The union of swath-wide bands around each line must cover [0, h]."""
        half = self.SWATH / 2.0
        self.assertLessEqual(ys[0], half + 1e-6, "gap along the bottom edge")
        self.assertGreaterEqual(ys[-1], height - half - 1e-6,
                                "gap along the top edge")
        for a, b in zip(ys, ys[1:]):
            self.assertLessEqual(b - a, self.SWATH + 1e-6,
                                 f"gap between passes at y={a:.2f} and y={b:.2f}")

    def test_height_29_5_needs_two_passes(self):
        ys = self._pass_line_ys(29.5)
        self.assertEqual(len(ys), 2)
        self.assert_full_coverage(ys, 29.5)

    def test_height_30_needs_two_passes(self):
        ys = self._pass_line_ys(30.0)
        self.assertEqual(len(ys), 2)
        self.assert_full_coverage(ys, 30.0)

    def test_exact_multiple_keeps_last_pass(self):
        # 210 / 20 is not exact but 210 - 10 is a swath multiple: under the
        # old anchor scheme the last line landed exactly on y_max, where the
        # half-open crossing rule dropped it float-noise-dependently. Every
        # line must now sit strictly inside the extent.
        ys = self._pass_line_ys(210.0)
        self.assertEqual(len(ys), 11)
        self.assertGreater(ys[0], 0.0)
        self.assertLess(ys[-1], 210.0)
        self.assert_full_coverage(ys, 210.0)

    def test_narrower_than_half_swath_flies_midline(self):
        # Sub-swath fields still get one pass, down the middle. The lat/lon
        # round-trip leaves nanometer noise, so compare with a tolerance.
        ys = self._pass_line_ys(6.0)
        self.assertEqual(len(ys), 1)
        self.assertAlmostEqual(ys[0], 3.0, places=6)


class TestValidation(unittest.TestCase):
    def test_too_few_vertices(self):
        with self.assertRaises(ValueError):
            plan_coverage(poly_ll([(0.0, 0.0), (100.0, 0.0)]),
                          swath_m=20.0, alt_m=100.0)

    def test_collinear_polygon_rejected(self):
        # Three points on a line enclose no area; must raise, not return an
        # empty "successful" plan.
        with self.assertRaises(ValueError):
            plan_coverage(poly_ll([(0.0, 0.0), (100.0, 0.0), (200.0, 0.0)]),
                          swath_m=20.0, alt_m=100.0)

    def test_identical_vertices_rejected(self):
        with self.assertRaises(ValueError):
            plan_coverage(poly_ll([(50.0, 50.0)] * 3),
                          swath_m=20.0, alt_m=100.0)

    def test_zero_swath(self):
        with self.assertRaises(ValueError):
            plan_coverage(poly_ll(RECT_XY), swath_m=0.0, alt_m=100.0)

    def test_negative_swath(self):
        with self.assertRaises(ValueError):
            plan_coverage(poly_ll(RECT_XY), swath_m=-5.0, alt_m=100.0)


class TestSpeedValidation(unittest.TestCase):
    """NaN/Infinity/out-of-range speed must map to a clean client error on
    BOTH coverage endpoints.

    Regression: speed=NaN used to pass the bare-float model, defeat
    plan_coverage's `speed_ms <= 0` guard (NaN <= 0 is False), poison
    est_time_s and crash response serialization — an unauthenticated HTTP
    500 (speed=Infinity returned a silently bogus est_time_s of 0.0).

    The check deliberately lives in the handlers (_check_speed), NOT as a
    pydantic Field(gt/le): this FastAPI/starlette stack echoes a rejected
    input inside the 422 body, so rejecting NaN via pydantic just moves the
    same JSON-serialization 500 into the validation-error handler.
    """

    BAD = (float("nan"), float("inf"), float("-inf"), 0.0, -5.0, 1.0, 80.5)

    def test_plan_rejects_bad_speed_with_serializable_422(self):
        import json

        from fastapi import HTTPException

        from app.routers.coverage import CoverageRequest, plan

        for bad in self.BAD:
            req = CoverageRequest(polygon=poly_ll(RECT_XY), swath=20.0,
                                  alt=100.0, speed=bad)
            with self.assertRaises(HTTPException,
                                   msg=f"speed={bad!r} not rejected") as ctx:
                plan(req)
            self.assertEqual(ctx.exception.status_code, 422)
            # The rejection itself must be JSON-serializable (the original
            # bug was a 500 born from an unserializable response body).
            json.dumps({"detail": ctx.exception.detail})

    def test_plan_auto_rejects_bad_speed_before_zone_fetch(self):
        from fastapi import HTTPException

        from app.routers import coverage as coverage_router

        def must_not_be_called(*a, **k):  # pragma: no cover
            raise AssertionError("fetch_zones called despite bad speed")

        from unittest import mock
        with mock.patch.object(coverage_router, "fetch_zones",
                               must_not_be_called):
            for bad in self.BAD:
                req = coverage_router.AutoCoverageRequest(
                    polygon=poly_ll(RECT_XY), swath=20.0, alt=100.0,
                    speed=bad)
                with self.assertRaises(
                        HTTPException,
                        msg=f"speed={bad!r} not rejected") as ctx:
                    coverage_router.plan_auto(req)
                self.assertEqual(ctx.exception.status_code, 422)

    def test_legal_speeds_still_plan(self):
        from app.routers.coverage import CoverageRequest, plan

        for ok in (1.5, 18.0, 80.0):
            resp = plan(CoverageRequest(polygon=poly_ll(RECT_XY), swath=20.0,
                                        alt=100.0, speed=ok))
            self.assertTrue(math.isfinite(resp["stats"]["est_time_s"]))

    def test_plan_coverage_rejects_non_finite_speed(self):
        # Defense in depth at the geometry layer for non-router callers.
        for bad in (float("nan"), float("inf"), float("-inf"), 0.0):
            with self.assertRaises(ValueError, msg=f"speed_ms={bad!r}"):
                plan_coverage(poly_ll(RECT_XY), swath_m=20.0, alt_m=100.0,
                              speed_ms=bad)


class TestRouterErrorMapping(unittest.TestCase):
    def test_zero_area_polygon_maps_to_400(self):
        # The /plan handler is plain sync, so call it directly: a degenerate
        # polygon must surface as a client error, not a 200 with no mission.
        from fastapi import HTTPException

        from app.routers.coverage import CoverageRequest, plan

        req = CoverageRequest(
            polygon=[ll_from_xy(x, 0.0) for x in (0.0, 100.0, 200.0)],
            swath=20.0, alt=100.0,
        )
        with self.assertRaises(HTTPException) as ctx:
            plan(req)
        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()

"""Unit tests for no-spray keepout clipping (app.coverage + coverage router).

Same conventions as test_coverage.py: polygons are authored in local ENU
meters around a Kansas reference point, converted to lat/lon with the same
equirectangular math the planner uses, and planner output is converted back
to meters for geometric checks. Buffer assertions use an INDEPENDENT
point-to-polygon distance implemented in this file (not imported from app
code) so a shared geometry bug can't self-certify.

No network anywhere: plan_auto tests monkeypatch the router's fetch_zones.
"""

import math
import unittest
from unittest import mock

from app.coverage import EARTH_RADIUS_M, plan_coverage
from app.routers import coverage as coverage_router

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


def dist_to_poly_xy(px: float, py: float,
                    poly_xy: list[tuple[float, float]]) -> float:
    """Independent point-to-polygon distance in meters (0.0 if inside).

    Deliberately re-implemented here (even-odd ray cast + min distance to
    edges) rather than reusing the planner's or gis_zones' helpers, so the
    keepout buffer assertions don't share code with the unit under test.
    """
    inside = False
    n = len(poly_xy)
    j = n - 1
    for i in range(n):
        xi, yi = poly_xy[i]
        xj, yj = poly_xy[j]
        if (yi > py) != (yj > py):
            if px < (xj - xi) * (py - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    if inside:
        return 0.0
    best = math.inf
    for i in range(n):
        ax, ay = poly_xy[i]
        bx, by = poly_xy[(i + 1) % n]
        dx, dy = bx - ax, by - ay
        len2 = dx * dx + dy * dy
        t = 0.0 if len2 == 0 else max(
            0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / len2))
        best = min(best, math.hypot(px - (ax + t * dx), py - (ay + t * dy)))
    return best


def segments_xy(plan: dict) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Waypoints come in (start, end) pairs — one pair per spray segment."""
    wps = [xy_from_ll(w["lat"], w["lon"]) for w in plan["waypoints"]]
    return [(wps[i], wps[i + 1]) for i in range(0, len(wps), 2)]


def sprayed_length(plan: dict) -> float:
    """Total on-sprayer length (segment lengths only, no transit hops)."""
    return sum(math.dist(a, b) for a, b in segments_xy(plan))


# 400 m x 200 m axis-aligned field; swath 20 + angle 0 -> pass lines at
# y = 10, 30, ..., 190 (matches the pinned behavior in test_coverage.py).
RECT_XY = [(0.0, 0.0), (400.0, 0.0), (400.0, 200.0), (0.0, 200.0)]
# 40 m x 40 m pond centered in the field: interior spans y in (80, 120), so
# with buffer 0 exactly the y=90 and y=110 passes cross it.
POND_XY = [(180.0, 80.0), (220.0, 80.0), (220.0, 120.0), (180.0, 120.0)]


def plan_rect(**kwargs) -> dict:
    return plan_coverage(poly_ll(RECT_XY), swath_m=20.0, alt_m=100.0,
                         angle_deg=0.0, **kwargs)


class TestPondClipping(unittest.TestCase):
    def setUp(self):
        self.legacy = plan_rect()
        self.plan0 = plan_rect(keepouts=[poly_ll(POND_XY)], keepout_buffer_m=0.0)
        self.plan20 = plan_rect(keepouts=[poly_ll(POND_XY)], keepout_buffer_m=20.0)

    def _segments_per_line(self, plan: dict) -> dict:
        counts: dict = {}
        for (sx, sy), (_ex, _ey) in segments_xy(plan):
            key = round(sy)
            counts[key] = counts.get(key, 0) + 1
        return counts

    def test_crossing_passes_split_into_two_subsegments(self):
        counts = self._segments_per_line(self.plan0)
        # Only the two passes through the pond interior split; all others
        # stay whole.
        for line_y in range(10, 200, 20):
            expected = 2 if line_y in (90, 110) else 1
            self.assertEqual(counts.get(line_y), expected,
                             f"line y={line_y}: {counts.get(line_y)} segments")
        self.assertEqual(self.plan0["stats"]["n_segments"], 12)
        self.assertEqual(self.plan0["stats"]["n_passes"], 10)
        self.assertEqual(self.plan0["stats"]["keepouts_applied"], 1)
        self.assertEqual(len(self.plan0["waypoints"]), 24)

    def test_buffer_20_all_waypoints_keep_standoff(self):
        # Independent numeric check: every waypoint at least 20 m (minus a
        # 0.5 m tolerance for projection round-trips) from the pond.
        for px, py in (xy_from_ll(w["lat"], w["lon"])
                       for w in self.plan20["waypoints"]):
            self.assertGreaterEqual(
                dist_to_poly_xy(px, py, POND_XY), 20.0 - 0.5,
                f"waypoint ({px:.2f}, {py:.2f}) inside the 20 m pond buffer")

    def test_buffer_20_whole_segments_keep_standoff(self):
        # The guarantee is for every sprayed POINT, not just endpoints:
        # sample the interior of each segment too (catches capsule-math
        # errors near the pond's corners).
        for (ax, ay), (bx, by) in segments_xy(self.plan20):
            for k in range(21):
                t = k / 20.0
                px, py = ax + t * (bx - ax), ay + t * (by - ay)
                self.assertGreaterEqual(
                    dist_to_poly_xy(px, py, POND_XY), 20.0 - 0.5,
                    f"sprayed point ({px:.2f}, {py:.2f}) inside buffer")

    def test_bigger_buffer_shrinks_sprayed_length(self):
        # Unclipped rectangle sprays 10 x 400 m; the pond removes 2 x 40 m
        # at buffer 0 and strictly more at buffer 20 (4 passes clipped).
        len_legacy = sprayed_length(self.legacy)
        len0 = sprayed_length(self.plan0)
        len20 = sprayed_length(self.plan20)
        self.assertAlmostEqual(len_legacy, 4000.0, delta=1.0)
        self.assertAlmostEqual(len0, 3920.0, delta=1.0)
        self.assertLess(len20, len0)
        self.assertLess(len0, len_legacy)
        # Buffer 20 also clips the y=70 and y=130 passes (10 m from the
        # pond edge) -> 14 segments total.
        self.assertEqual(self.plan20["stats"]["n_segments"], 14)

    def test_serpentine_direction_survives_clipping(self):
        # Sub-segments must keep their parent pass's flight direction:
        # consecutive pass lines still alternate east/west.
        segs = segments_xy(self.plan0)
        dir_by_line: dict = {}
        for (sx, sy), (ex, _ey) in segs:
            key = round(sy)
            d = 1.0 if ex > sx else -1.0
            # All sub-segments on one line fly the same way.
            self.assertEqual(dir_by_line.setdefault(key, d), d)
        lines = sorted(dir_by_line)
        for a, b in zip(lines, lines[1:]):
            self.assertLess(dir_by_line[a] * dir_by_line[b], 0.0,
                            "consecutive pass lines must alternate direction")


class TestKeepoutValidation(unittest.TestCase):
    def test_malformed_keepout_two_points(self):
        with self.assertRaises(ValueError):
            plan_rect(keepouts=[poly_ll([(0.0, 0.0), (50.0, 50.0)])])

    def test_negative_buffer(self):
        with self.assertRaises(ValueError):
            plan_rect(keepouts=[poly_ll(POND_XY)], keepout_buffer_m=-1.0)

    def test_keepout_vertices_must_be_latlon_dicts(self):
        with self.assertRaises(ValueError):
            plan_rect(keepouts=[[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]])

    def test_full_field_keepout_raises(self):
        blanket = poly_ll([(-50.0, -50.0), (450.0, -50.0),
                           (450.0, 250.0), (-50.0, 250.0)])
        with self.assertRaisesRegex(ValueError,
                                    "field fully blocked by keepout zones"):
            plan_rect(keepouts=[blanket])

    def test_request_model_caps_keepout_sizes(self):
        # DoS guard at the request-model layer (mirrors the polygon cap):
        # clipping cost is linear in passes x total keepout edges, so both
        # the ring count and the per-ring vertex count must be bounded
        # before any geometry runs.
        from pydantic import ValidationError

        tri = poly_ll([(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)])
        base = dict(polygon=poly_ll(RECT_XY), swath=20.0, alt=100.0)
        with self.assertRaises(ValidationError):
            coverage_router.CoverageRequest(**base, keepouts=[tri] * 101)
        big_ring = poly_ll([(float(i), float(i % 2)) for i in range(501)])
        with self.assertRaises(ValidationError):
            coverage_router.CoverageRequest(**base, keepouts=[big_ring])
        # At the caps the request is still accepted (limits, not off-by-one).
        coverage_router.CoverageRequest(**base, keepouts=[tri] * 100)

    def test_subsegments_under_2m_are_dropped(self):
        # Keepout covering all but the last 1 m of every pass: the 1 m
        # slivers are below the 2 m minimum, so nothing sprayable remains.
        almost_all = poly_ll([(-20.0, -20.0), (399.0, -20.0),
                              (399.0, 220.0), (-20.0, 220.0)])
        with self.assertRaisesRegex(ValueError,
                                    "field fully blocked by keepout zones"):
            plan_rect(keepouts=[almost_all])
        # Widen the gap to 3 m and the slivers survive as real segments.
        most = poly_ll([(-20.0, -20.0), (397.0, -20.0),
                        (397.0, 220.0), (-20.0, 220.0)])
        plan = plan_rect(keepouts=[most])
        self.assertEqual(plan["stats"]["n_segments"], 10)
        self.assertAlmostEqual(sprayed_length(plan), 30.0, delta=0.5)


class TestLegacyRegression(unittest.TestCase):
    """Calls without keepouts must be indistinguishable from the old planner."""

    def test_legacy_stats_shape_and_values(self):
        plan = plan_coverage(poly_ll(RECT_XY), swath_m=20.0, alt_m=100.0)
        # Exactly the pre-keepout stats keys — no keepouts_applied/n_segments.
        self.assertEqual(
            set(plan["stats"]),
            {"area_m2", "area_acres", "n_passes", "path_length_m",
             "est_time_s", "swath_m", "angle_deg"})
        # Pins from test_coverage.py's rectangle expectations.
        self.assertEqual(plan["stats"]["n_passes"], 10)
        self.assertEqual(len(plan["waypoints"]), 20)
        self.assertAlmostEqual(plan["stats"]["area_acres"], 19.768,
                               delta=0.01 * 19.768)
        expected_len = 10 * 400 + 9 * 20
        self.assertAlmostEqual(plan["stats"]["path_length_m"], expected_len,
                               delta=0.05 * expected_len)

    def test_router_without_keepout_fields_is_byte_for_byte(self):
        # A request that never mentions keepouts must produce exactly the
        # same dict the pure planner produces (no new keys, same floats).
        req = coverage_router.CoverageRequest(
            polygon=poly_ll(RECT_XY), swath=20.0, alt=100.0)
        via_router = coverage_router.plan(req)
        direct = plan_coverage(poly_ll(RECT_XY), swath_m=20.0, alt_m=100.0,
                               angle_deg=None, speed_ms=18.0)
        self.assertEqual(via_router, direct)

    def test_empty_keepout_list_only_adds_stats_keys(self):
        legacy = plan_rect()
        with_empty = plan_rect(keepouts=[])
        self.assertEqual(with_empty["waypoints"], legacy["waypoints"])
        self.assertEqual(with_empty["stats"]["keepouts_applied"], 0)
        self.assertEqual(with_empty["stats"]["n_segments"],
                         legacy["stats"]["n_passes"])

    def test_router_maps_fully_blocked_to_400(self):
        from fastapi import HTTPException

        req = coverage_router.CoverageRequest(
            polygon=poly_ll(RECT_XY), swath=20.0, alt=100.0, angle=0.0,
            keepouts=[poly_ll([(-50.0, -50.0), (450.0, -50.0),
                               (450.0, 250.0), (-50.0, 250.0)])])
        with self.assertRaises(HTTPException) as ctx:
            coverage_router.plan(req)
        self.assertEqual(ctx.exception.status_code, 400)


def _pond_zone() -> dict:
    """A gis_zones-shaped water zone: closed ring (first point == last)."""
    ring = poly_ll(POND_XY)
    ring.append(dict(ring[0]))
    return {"kind": "water", "coords": ring, "tags": {"natural": "water"}}


class TestPlanAuto(unittest.TestCase):
    """plan_auto with fetch_zones monkeypatched — no Overpass traffic."""

    def _request(self, **overrides) -> "coverage_router.AutoCoverageRequest":
        kwargs = dict(polygon=poly_ll(RECT_XY), swath=20.0, alt=100.0,
                      angle=0.0)
        kwargs.update(overrides)
        return coverage_router.AutoCoverageRequest(**kwargs)

    def test_happy_path_clips_with_water_buffer(self):
        zones = {"water": [_pond_zone()], "trees": [], "buildings": [],
                 "source": "overpass"}
        calls = {}

        def fake_fetch(lat, lon, radius_m=2000):
            calls["args"] = (lat, lon, radius_m)
            return zones

        with mock.patch.object(coverage_router, "fetch_zones", fake_fetch):
            resp = coverage_router.plan_auto(
                self._request(water_buffer=20.0))

        self.assertFalse(resp["zones_unavailable"])
        self.assertEqual(len(resp["zones"]["water"]), 1)
        self.assertEqual(resp["stats"]["keepouts_applied"], 1)
        # Only water zones present, so max-buffer == water_buffer == 20 m;
        # verify the standoff with the independent distance function.
        for px, py in (xy_from_ll(w["lat"], w["lon"])
                       for w in resp["waypoints"]):
            self.assertGreaterEqual(dist_to_poly_xy(px, py, POND_XY),
                                    20.0 - 0.5)
        # Search radius: half the 400x200 field diagonal (~223.6 m) + 500 m.
        lat, lon, radius = calls["args"]
        self.assertAlmostEqual(radius, math.hypot(400, 200) / 2 + 500,
                               delta=2.0)
        # Centroid of the rectangle sits at local (200, 100).
        cx, cy = xy_from_ll(lat, lon)
        self.assertAlmostEqual(cx, 200.0, delta=1.0)
        self.assertAlmostEqual(cy, 100.0, delta=1.0)

    def test_no_zones_found_still_plans(self):
        empty = {"water": [], "trees": [], "buildings": [],
                 "source": "overpass"}
        with mock.patch.object(coverage_router, "fetch_zones",
                               lambda *a, **k: empty):
            resp = coverage_router.plan_auto(self._request())
        self.assertFalse(resp["zones_unavailable"])
        self.assertEqual(resp["stats"]["keepouts_applied"], 0)
        self.assertEqual(resp["stats"]["n_segments"],
                         resp["stats"]["n_passes"])

    def test_overpass_failure_degrades_to_unclipped_plan(self):
        def boom(*args, **kwargs):
            raise RuntimeError("Overpass API request failed after retry")

        with mock.patch.object(coverage_router, "fetch_zones", boom):
            resp = coverage_router.plan_auto(self._request())

        self.assertTrue(resp["zones_unavailable"])
        self.assertEqual(resp["zones"], {})
        # The fallback is the exact legacy (unclipped) plan: same waypoints,
        # no keepout stats keys.
        legacy = plan_rect()
        self.assertEqual(resp["waypoints"], legacy["waypoints"])
        self.assertNotIn("keepouts_applied", resp["stats"])
        self.assertNotIn("n_segments", resp["stats"])


if __name__ == "__main__":
    unittest.main()

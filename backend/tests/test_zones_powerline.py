"""Powerline keepouts (OSM power=line / power=minor_line) + the linear-feature
corridor path they share with waterways.

Powerlines are the one keepout kind that protects the AIRFRAME rather than
spray quality: a low ag pass clipping a line is a crash, not a wasted pass.
That difference shows up here as a wider default buffer and as an insistence
that an underground line is NOT a keepout (it can't be hit, and carving real
acreage out of a field for it is a cost with no safety benefit).

These tests also cover the waterway corridor path, which shipped in the
2026-08-15 hardening pass with no unit coverage at all — powerline generalizes
that exact branch, so both are exercised through the same assertions.
"""

import unittest
from unittest import mock

from app import gis_zones
from app.gis_zones import _build_query, _corridor_ring, _parse_overpass
from app.routers import coverage as cov
from app.routers import coverage_multi as cm


def _geom(*pairs):
    """Overpass-style 'out geom' point list from (lat, lon) tuples."""
    return [{"lat": la, "lon": lo} for la, lo in pairs]


# A straight run of line crossing the field area, plus the decoys that must
# NOT become keepouts.
_LINE = _geom((39.00, -95.00), (39.00, -95.01), (39.00, -95.02))

_FIXTURE = {
    "elements": [
        # Overhead distribution feeder -> corridor keepout.
        {"type": "way", "id": 10, "tags": {"power": "minor_line"},
         "geometry": _LINE},
        # Transmission line -> corridor keepout.
        {"type": "way", "id": 11, "tags": {"power": "line"},
         "geometry": _geom((39.10, -95.00), (39.10, -95.01))},
        # Buried: no collision risk, must be ignored entirely.
        {"type": "way", "id": 12,
         "tags": {"power": "line", "location": "underground"},
         "geometry": _geom((39.20, -95.00), (39.20, -95.01))},
        # Point/other power features we deliberately never query for.
        {"type": "way", "id": 13, "tags": {"power": "cable"},
         "geometry": _geom((39.30, -95.00), (39.30, -95.01))},
        {"type": "way", "id": 14, "tags": {"power": "tower"},
         "geometry": _geom((39.40, -95.00), (39.40, -95.01))},
        # Linear waterway -> corridor keepout (the pattern powerline reuses).
        {"type": "way", "id": 15, "tags": {"waterway": "ditch"},
         "geometry": _geom((39.50, -95.00), (39.50, -95.01), (39.50, -95.02))},
        # A power RELATION should never arrive (we query ways only); if one
        # does, outer-ring stitching is the wrong shape for a linear feature.
        {"type": "relation", "id": 16, "tags": {"power": "line"},
         "members": [{"type": "way", "role": "outer",
                      "geometry": _geom((39.60, -95.00), (39.60, -95.01),
                                        (39.601, -95.01))}]},
    ],
}


class QueryTests(unittest.TestCase):
    def test_power_clause_present(self):
        q = _build_query(39.0, -95.0, 2000)
        self.assertIn('way["power"~"^(line|minor_line)$"]', q)

    def test_still_one_round_trip(self):
        # Powerlines must ride along in the SAME Overpass request — an extra
        # round-trip per plan would double the load on a shared free service.
        self.assertEqual(_build_query(39.0, -95.0, 2000).count("out geom;"), 1)


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.zones = _parse_overpass(_FIXTURE)

    def test_overhead_lines_become_powerline_zones(self):
        self.assertEqual(len(self.zones["powerline"]), 2)  # ids 10, 11
        for z in self.zones["powerline"]:
            self.assertEqual(z["kind"], "powerline")

    def test_underground_line_is_not_a_keepout(self):
        tagged = [z["tags"] for z in self.zones["powerline"]]
        self.assertNotIn({"power": "line", "location": "underground"}, tagged)

    def test_cable_and_tower_ignored(self):
        kinds = {z["tags"].get("power") for z in self.zones["powerline"]}
        self.assertEqual(kinds, {"line", "minor_line"})

    def test_power_relation_skipped(self):
        # id 16 is the only relation; nothing from it should appear.
        for z in self.zones["powerline"]:
            self.assertIn(z["tags"].get("power"), ("line", "minor_line"))
        self.assertEqual(len(self.zones["powerline"]), 2)

    def test_linear_waterway_still_corridors(self):
        # Regression guard for the branch powerline generalized.
        self.assertEqual(len(self.zones["water"]), 1)  # id 15

    def test_all_rings_closed(self):
        for kind in ("water", "trees", "buildings", "powerline"):
            for z in self.zones[kind]:
                coords = z["coords"]
                self.assertGreaterEqual(len(coords), 4)
                self.assertEqual(coords[0], coords[-1])

    def test_powerline_key_always_present(self):
        # Callers index zones["powerline"] unconditionally.
        self.assertIn("powerline", _parse_overpass({"elements": []}))


class CorridorRingTests(unittest.TestCase):
    """The out-and-back trick: a zero-area ring the planner's Minkowski
    buffer turns into a corridor of buffer_m around the line."""

    def test_traces_out_and_back(self):
        ring = _corridor_ring(_LINE)
        self.assertEqual(ring[0], ring[-1])
        self.assertEqual(len(ring), 2 * len(_LINE) - 1)

    def test_two_point_line_gains_midpoint(self):
        # A bare [a, b] would be dropped by callers' len >= 3 check.
        ring = _corridor_ring(_geom((39.0, -95.0), (39.0, -95.01)))
        distinct = {(p["lat"], p["lon"]) for p in ring}
        self.assertEqual(len(distinct), 3)

    def test_single_point_rejected(self):
        self.assertIsNone(_corridor_ring(_geom((39.0, -95.0))))


class PlanAutoBufferTests(unittest.TestCase):
    """plan_auto: powerline_buffer is plumbed and only raises the clip
    distance when a line is actually present."""

    FIELD = [{"lat": 39.000, "lon": -95.000},
             {"lat": 39.002, "lon": -95.000},
             {"lat": 39.002, "lon": -95.003},
             {"lat": 39.000, "lon": -95.003}]

    def _capture_buffer(self, zones):
        seen = {}

        def fake_plan(poly, **kw):
            seen.update(kw)
            return {"waypoints": [], "passes": [], "stats": {}}

        req = cov.AutoCoverageRequest(polygon=self.FIELD)
        with mock.patch.object(cov, "fetch_zones", return_value=zones), \
                mock.patch.object(cov, "plan_coverage", side_effect=fake_plan):
            out = cov.plan_auto(req)
        return seen, out

    def test_powerline_present_uses_wider_buffer(self):
        zones = {"water": [], "trees": [], "buildings": [],
                 "powerline": [{"kind": "powerline", "coords": _corridor_ring(
                     _LINE) + [], "tags": {"power": "minor_line"}}]}
        seen, out = self._capture_buffer(zones)
        self.assertEqual(seen["keepout_buffer_m"], 20.0)
        self.assertTrue(out["zones"]["powerline"])

    def test_no_powerline_leaves_buffer_alone(self):
        square = [{"lat": 39.0005, "lon": -95.0005},
                  {"lat": 39.0006, "lon": -95.0005},
                  {"lat": 39.0006, "lon": -95.0006},
                  {"lat": 39.0005, "lon": -95.0006},
                  {"lat": 39.0005, "lon": -95.0005}]
        zones = {"water": [{"kind": "water", "coords": square, "tags": {}}],
                 "trees": [], "buildings": [], "powerline": []}
        seen, _ = self._capture_buffer(zones)
        self.assertEqual(seen["keepout_buffer_m"], 15.0)  # water only


class PlanMultiBufferTests(unittest.TestCase):
    """plan_multi's clip buffer is presence-aware.

    It used to be an unconditional max() over every per-kind field, so simply
    adding the wider powerline default would have silently widened EVERY
    keepout in EVERY job by 5 m — losing real acreage to a hazard that isn't
    there. These two tests are what pin that down.
    """

    FIELD = [{"lat": 39.900, "lon": -95.800},
             {"lat": 39.901, "lon": -95.800},
             {"lat": 39.901, "lon": -95.802},
             {"lat": 39.900, "lon": -95.802}]

    def _capture(self, zones):
        seen = {}

        def fake_multi(fields, swath, alt, **kw):
            seen.update(kw)
            return {"fields": [], "transits": [], "totals": {},
                    "flight_order": [], "combined_waypoints": []}

        req = cm.MultiRequest(fields=[self.FIELD])
        with mock.patch.object(cm, "fetch_zones", return_value=zones), \
                mock.patch.object(cm, "plan_multi", side_effect=fake_multi):
            cm.plan_multi_endpoint(req)
        return seen

    def _square(self):
        return [{"lat": 39.9005, "lon": -95.8005},
                {"lat": 39.9006, "lon": -95.8005},
                {"lat": 39.9006, "lon": -95.8006},
                {"lat": 39.9005, "lon": -95.8006},
                {"lat": 39.9005, "lon": -95.8005}]

    def test_trees_only_does_not_inherit_powerline_default(self):
        zones = {"water": [], "buildings": [], "powerline": [],
                 "trees": [{"kind": "trees", "coords": self._square(),
                            "tags": {}}]}
        self.assertEqual(self._capture(zones)["keepout_buffer_m"], 10.0)

    def test_powerline_present_widens_to_clearance(self):
        zones = {"water": [], "buildings": [], "trees": [],
                 "powerline": [{"kind": "powerline",
                                "coords": _corridor_ring(_LINE),
                                "tags": {"power": "line"}}]}
        self.assertEqual(self._capture(zones)["keepout_buffer_m"], 20.0)


class FetchZonesShapeTests(unittest.TestCase):
    def setUp(self):
        gis_zones._cache.clear()

    def test_fetch_returns_powerline_bucket(self):
        with mock.patch.object(gis_zones, "_overpass_post",
                               return_value=_FIXTURE):
            zones = gis_zones.fetch_zones(39.0, -95.0, 2000)
        self.assertEqual(len(zones["powerline"]), 2)
        self.assertEqual(zones["source"], "overpass")


if __name__ == "__main__":
    unittest.main()

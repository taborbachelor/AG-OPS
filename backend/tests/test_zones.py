"""Tests for the OSM no-spray-zone layer (app.gis_zones).

All network access is stubbed: the parser gets a canned Overpass-style
payload, and the cache test patches the low-level POST helper.
"""

import math
import unittest
from unittest import mock

from app import gis_zones
from app.gis_zones import dist_to_zone_m, fetch_zones, _parse_overpass

# ~100 m square centred on (39.0, -95.0); degree offsets derived from the
# same equirectangular scale the module uses, so expected distances in the
# tests are exact by construction.
_LAT0, _LON0 = 39.0, -95.0
_DLAT = 100.0 / 111_320.0
_DLON = 100.0 / (111_320.0 * math.cos(math.radians(_LAT0)))


def _square_zone() -> dict:
    """Closed 100 m-half-width square zone around (_LAT0, _LON0)."""
    corners = [
        (_LAT0 - _DLAT, _LON0 - _DLON),
        (_LAT0 - _DLAT, _LON0 + _DLON),
        (_LAT0 + _DLAT, _LON0 + _DLON),
        (_LAT0 + _DLAT, _LON0 - _DLON),
        (_LAT0 - _DLAT, _LON0 - _DLON),  # closed
    ]
    return {
        "kind": "water",
        "coords": [{"lat": la, "lon": lo} for la, lo in corners],
        "tags": {"natural": "water"},
    }


class DistToZoneTests(unittest.TestCase):
    def test_point_inside_returns_zero(self):
        self.assertEqual(
            dist_to_zone_m({"lat": _LAT0, "lon": _LON0}, _square_zone()), 0.0)

    def test_point_100m_east_of_edge(self):
        # East edge sits +100 m from centre; the point sits another
        # +100 m east of that edge.
        pt = {"lat": _LAT0, "lon": _LON0 + 2 * _DLON}
        d = dist_to_zone_m(pt, _square_zone())
        self.assertAlmostEqual(d, 100.0, delta=2.0)


def _ring(*pairs):
    """Overpass-style geometry list from (lat, lon) tuples."""
    return [{"lat": la, "lon": lo} for la, lo in pairs]


# Canned Overpass 'out geom' response covering every parser branch.
_FIXTURE = {
    "elements": [
        # Unclosed water way -> should be closed by the parser.
        {"type": "way", "id": 1, "tags": {"natural": "water"},
         "geometry": _ring((39.0, -95.0), (39.0, -95.001),
                           (39.001, -95.001), (39.001, -95.0))},
        # Already-closed forest way.
        {"type": "way", "id": 2, "tags": {"landuse": "forest"},
         "geometry": _ring((39.1, -95.0), (39.1, -95.001),
                           (39.101, -95.001), (39.1, -95.0))},
        # Building way (unclosed).
        {"type": "way", "id": 3, "tags": {"building": "yes"},
         "geometry": _ring((39.2, -95.0), (39.2, -95.0001),
                           (39.2001, -95.0001), (39.2001, -95.0))},
        # Water relation: outer member has geometry (kept), inner ignored.
        {"type": "relation", "id": 4, "tags": {"natural": "water"},
         "members": [
             {"type": "way", "role": "outer",
              "geometry": _ring((39.3, -95.0), (39.3, -95.002),
                                (39.302, -95.002), (39.302, -95.0))},
             {"type": "way", "role": "inner",
              "geometry": _ring((39.3005, -95.0005), (39.3005, -95.001),
                                (39.301, -95.001), (39.301, -95.0005))},
         ]},
        # Relation with no member geometry -> skipped entirely.
        {"type": "relation", "id": 5, "tags": {"landuse": "forest"},
         "members": [{"type": "way", "role": "outer"}]},
        # Degenerate 2-point way -> skipped (<4 points after closing).
        {"type": "way", "id": 6, "tags": {"natural": "wetland"},
         "geometry": _ring((39.4, -95.0), (39.4, -95.001))},
        # Untagged-for-us way -> ignored.
        {"type": "way", "id": 7, "tags": {"highway": "residential"},
         "geometry": _ring((39.5, -95.0), (39.5, -95.001),
                           (39.501, -95.001), (39.501, -95.0))},
    ],
}


class ParserTests(unittest.TestCase):
    def test_counts_per_kind(self):
        zones = _parse_overpass(_FIXTURE)
        self.assertEqual(len(zones["water"]), 2)     # way 1 + relation 4 outer
        self.assertEqual(len(zones["trees"]), 1)     # way 2 only
        self.assertEqual(len(zones["buildings"]), 1)

    def test_all_rings_closed_and_valid(self):
        zones = _parse_overpass(_FIXTURE)
        for kind in ("water", "trees", "buildings"):
            for zone in zones[kind]:
                coords = zone["coords"]
                self.assertGreaterEqual(len(coords), 4)
                self.assertEqual(coords[0], coords[-1],
                                 f"{kind} ring not closed: {coords}")
                self.assertEqual(zone["kind"], kind)

    def test_unclosed_way_gains_closing_point(self):
        zones = _parse_overpass(_FIXTURE)
        water_way = zones["water"][0]  # element id 1: 4 open points in
        self.assertEqual(len(water_way["coords"]), 5)  # ... 5 closed out


class CacheTests(unittest.TestCase):
    def setUp(self):
        # Each test starts cold so hit/miss behaviour is deterministic.
        gis_zones._cache.clear()

    def test_second_identical_call_skips_network(self):
        with mock.patch.object(
                gis_zones, "_overpass_post",
                return_value=_FIXTURE) as fake_post:
            first = fetch_zones(39.9042, -95.7997, 2000)
            second = fetch_zones(39.9042, -95.7997, 2000)
        self.assertEqual(fake_post.call_count, 1)
        self.assertEqual(first["source"], "overpass")
        self.assertEqual(second, first)

    def test_different_location_misses_cache(self):
        with mock.patch.object(
                gis_zones, "_overpass_post",
                return_value={"elements": []}) as fake_post:
            fetch_zones(39.9042, -95.7997, 2000)
            fetch_zones(40.5, -96.0, 2000)
        self.assertEqual(fake_post.call_count, 2)

    def test_radius_capped_at_5km(self):
        with mock.patch.object(
                gis_zones, "_overpass_post",
                return_value={"elements": []}) as fake_post:
            fetch_zones(39.0, -95.0, 999_999)
        query = fake_post.call_args[0][0]
        self.assertIn("around:5000,", query)


if __name__ == "__main__":
    unittest.main()

"""Hardening tests for app.gis_zones: radius validation + cache bounds.

Covers the review fixes: a NaN/inf/non-positive radius must be rejected
before it can crash int() or reach Overpass, and the in-memory cache must
not grow without bound (TTL sweep + size cap). All network access is
stubbed, same as test_zones.py.
"""

import unittest
from unittest import mock

from fastapi import HTTPException

from app import gis_zones
from app.gis_zones import fetch_zones
from app.routers.zones import get_zones

_EMPTY = {"elements": []}


class RadiusValidationTests(unittest.TestCase):
    def setUp(self):
        gis_zones._cache.clear()

    def test_bad_radii_raise_value_error_without_network(self):
        for bad in (float("nan"), float("inf"), float("-inf"), 0, -500):
            with mock.patch.object(
                    gis_zones, "_overpass_post",
                    return_value=_EMPTY) as fake_post:
                with self.assertRaises(ValueError, msg=f"radius={bad!r}"):
                    fetch_zones(39.0, -95.0, bad)
            # Bad input must be rejected before any Overpass round-trip.
            self.assertEqual(fake_post.call_count, 0, f"radius={bad!r}")

    def test_router_maps_bad_radius_to_422(self):
        # Pydantic 2.x accepts 'nan' for a float query param, so the
        # handler itself must turn it into a client error, not a 500.
        with mock.patch.object(gis_zones, "_overpass_post",
                               return_value=_EMPTY):
            with self.assertRaises(HTTPException) as ctx:
                get_zones(39.0, -95.0, float("nan"))
        self.assertEqual(ctx.exception.status_code, 422)

    def test_valid_radius_still_works(self):
        with mock.patch.object(gis_zones, "_overpass_post",
                               return_value=_EMPTY):
            result = get_zones(39.0, -95.0, 2000)
        self.assertEqual(result["source"], "overpass")
        self.assertEqual(result["counts"],
                         {"water": 0, "trees": 0, "buildings": 0})


class CacheBoundsTests(unittest.TestCase):
    def setUp(self):
        gis_zones._cache.clear()

    def test_expired_entries_are_swept_on_fetch(self):
        stale_key = (10.0, 10.0, 1000)
        stale_ts = 0.0  # far past the 10-minute TTL
        gis_zones._cache[stale_key] = (stale_ts, {"source": "overpass"})
        with mock.patch.object(gis_zones, "_overpass_post",
                               return_value=_EMPTY):
            fetch_zones(39.0, -95.0, 2000)
        # The unrelated stale entry is gone, not just skipped on read.
        self.assertNotIn(stale_key, gis_zones._cache)

    def test_cache_size_stays_capped(self):
        with mock.patch.object(gis_zones, "_overpass_post",
                               return_value=_EMPTY):
            # Distinct locations -> distinct keys, all inside the TTL.
            for i in range(gis_zones._CACHE_MAX_ENTRIES + 50):
                fetch_zones(30.0 + i * 0.01, -95.0, 2000)
        self.assertLessEqual(len(gis_zones._cache),
                             gis_zones._CACHE_MAX_ENTRIES)

    def test_oldest_entry_evicted_first(self):
        with mock.patch.object(gis_zones, "_overpass_post",
                               return_value=_EMPTY):
            for i in range(gis_zones._CACHE_MAX_ENTRIES + 1):
                fetch_zones(30.0 + i * 0.01, -95.0, 2000)
        first_key = (30.0, -95.0, 2000)
        self.assertNotIn(first_key, gis_zones._cache)

    def test_fresh_hit_still_skips_network(self):
        # Regression guard: pruning must not break normal cache hits.
        with mock.patch.object(gis_zones, "_overpass_post",
                               return_value=_EMPTY) as fake_post:
            fetch_zones(39.9042, -95.7997, 2000)
            fetch_zones(39.9042, -95.7997, 2000)
        self.assertEqual(fake_post.call_count, 1)


if __name__ == "__main__":
    unittest.main()

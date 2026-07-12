"""Albers projection + CDL field segmentation (no network)."""

import math
import unittest
from unittest import mock

from app.albers import to_albers, from_albers
from app import cdl


class TestAlbers(unittest.TestCase):
    POINTS = [(39.9042, -95.7997),   # Sabetha KS
              (34.05, -118.24),      # Los Angeles
              (40.71, -74.00),       # New York
              (29.5, -96.0),         # on a standard parallel / central meridian
              (45.5, -96.0)]

    def test_roundtrip(self):
        for lat, lon in self.POINTS:
            x, y = to_albers(lat, lon)
            lat2, lon2 = from_albers(x, y)
            self.assertAlmostEqual(lat, lat2, places=6)
            self.assertAlmostEqual(lon, lon2, places=6)

    def test_local_scale_is_metric(self):
        # 30 m north in projected space should be ~30 m of ground distance.
        lat, lon = 39.9042, -95.7997
        x, y = to_albers(lat, lon)
        lat2, _ = from_albers(x, y + 30.0)
        ground = (lat2 - lat) * 111320.0
        self.assertAlmostEqual(ground, 30.0, delta=1.0)


def synth_grid():
    """40x40: two corn fields separated by a water strip, plus a tiny blob
    (below the size floor) and forest border."""
    W = H = 40
    g = [[141] * W for _ in range(H)]                # forest everywhere
    for r in range(5, 35):
        for c in range(4, 16):
            g[r][c] = 1                              # corn field A (30x12)
        for c in range(22, 36):
            g[r][c] = 5                              # soybean field B (30x14)
    for r in range(H):
        g[r][18] = 111                               # water strip
    for r, c in ((2, 2), (2, 3)):
        g[r][c] = 1                                  # 2-px blob: ignored
    return g


class TestDetect(unittest.TestCase):
    def _run(self, grid):
        ulx, uly = to_albers(39.92, -95.82)          # NW anchor near Sabetha
        gt = (ulx, uly, 30.0, 30.0)
        sel = [{"lat": 39.92, "lon": -95.82}, {"lat": 39.92, "lon": -95.80},
               {"lat": 39.90, "lon": -95.80}, {"lat": 39.90, "lon": -95.82}]
        with mock.patch.object(cdl, "_fetch_clip", return_value=(grid, gt)):
            cdl._cache.clear()
            return cdl.detect_fields_cdl(sel, year=2025)

    def test_two_fields_traced(self):
        out = self._run(synth_grid())
        self.assertEqual(len(out["fields"]), 2)
        a, b = sorted(out["fields"], key=lambda f: -f["acres"])
        # 30x14=420px and 30x12=360px at ~0.2224 ac/px
        self.assertAlmostEqual(a["acres"], 420 * cdl.ACRES_PER_PX, delta=1.0)
        self.assertAlmostEqual(b["acres"], 360 * cdl.ACRES_PER_PX, delta=1.0)
        self.assertEqual(a["crop"], "Soybeans")
        self.assertEqual(b["crop"], "Corn")
        for f in out["fields"]:
            self.assertGreaterEqual(len(f["polygon"]), 3)
            for p in f["polygon"]:
                self.assertTrue(39.89 < p["lat"] < 39.93)
                self.assertTrue(-95.83 < p["lon"] < -95.79)

    def test_water_and_forest_excluded(self):
        out = self._run(synth_grid())
        # No polygon vertex should sit on the water strip column (col 18):
        # fields A ends col 15, B starts col 22 — traced boundaries stay off it.
        # Verified indirectly: exactly two components, none spanning the strip.
        self.assertEqual(len(out["fields"]), 2)

    def test_selection_too_large_raises(self):
        sel = [{"lat": 39.9, "lon": -95.9}, {"lat": 40.2, "lon": -95.9},
               {"lat": 40.2, "lon": -95.5}, {"lat": 39.9, "lon": -95.5}]
        with self.assertRaises(ValueError):
            cdl.detect_fields_cdl(sel, year=2025)


if __name__ == "__main__":
    unittest.main()

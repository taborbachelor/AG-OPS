"""Albers projection + CDL field segmentation (no network)."""

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
        lat, lon = 39.9042, -95.7997
        x, y = to_albers(lat, lon)
        lat2, _ = from_albers(x, y + 30.0)
        ground = (lat2 - lat) * 111320.0
        self.assertAlmostEqual(ground, 30.0, delta=1.0)


def synth_grid():
    """40x40: corn field A (perfect rectangle), soybean field B with a 3x3
    pond inside it, a water strip between them, single-pixel speckle in A,
    and forest everywhere else."""
    W = H = 40
    g = [[141] * W for _ in range(H)]                # forest
    for r in range(5, 35):
        for c in range(4, 16):
            g[r][c] = 1                              # corn A: 30x12 = 360 px
        for c in range(22, 36):
            g[r][c] = 5                              # soy B: 30x14 = 420 px
    for r in range(15, 18):
        for c in range(27, 30):
            g[r][c] = 111                            # pond inside B: 3x3
    g[10][10] = 111                                  # 1px speckle in A (filled)
    for r in range(H):
        g[r][18] = 111                               # water strip
    return g


class TestEdgeLoops(unittest.TestCase):
    def test_rectangle_is_exact(self):
        comp = [(r, c) for r in range(2, 6) for c in range(3, 8)]  # 4x5
        loops = cdl._edge_loops(comp)
        self.assertEqual(len(loops), 1)
        self.assertAlmostEqual(cdl._loop_area_px(loops[0]), 20.0)
        corners = cdl._collapse_collinear(loops[0])
        self.assertEqual(len(corners), 4)   # a rectangle collapses to 4 corners
        self.assertEqual(set(corners), {(3, 2), (8, 2), (8, 6), (3, 6)})

    def test_hole_area_invariant(self):
        # 6x6 block with a 2x2 hole => 32 cells; outer 36, hole 4.
        comp = [(r, c) for r in range(6) for c in range(6)
                if not (2 <= r < 4 and 2 <= c < 4)]
        loops = sorted(cdl._edge_loops(comp), key=cdl._loop_area_px, reverse=True)
        self.assertEqual(len(loops), 2)
        self.assertAlmostEqual(cdl._loop_area_px(loops[0]), 36.0)
        self.assertAlmostEqual(cdl._loop_area_px(loops[1]), 4.0)
        self.assertAlmostEqual(
            cdl._loop_area_px(loops[0]) - cdl._loop_area_px(loops[1]), len(comp))


class TestDetect(unittest.TestCase):
    def _run(self, grid):
        ulx, uly = to_albers(39.92, -95.82)
        gt = (ulx, uly, 30.0, 30.0)
        sel = [{"lat": 39.92, "lon": -95.82}, {"lat": 39.92, "lon": -95.80},
               {"lat": 39.90, "lon": -95.80}, {"lat": 39.90, "lon": -95.82}]
        with mock.patch.object(cdl, "_fetch_clip", return_value=(grid, gt)):
            cdl._cache.clear()
            return cdl.detect_fields_cdl(sel, year=2025)

    def test_two_fields_with_pond_hole(self):
        out = self._run(synth_grid())
        self.assertEqual(len(out["fields"]), 2)
        b, a = sorted(out["fields"], key=lambda f: -f["acres"])
        # B: 420 - 9 pond px = 411; A: 360 (speckle filled by despeckle).
        self.assertAlmostEqual(b["acres"], 411 * cdl.ACRES_PER_PX, delta=1.0)
        self.assertAlmostEqual(a["acres"], 360 * cdl.ACRES_PER_PX, delta=1.0)
        self.assertEqual(b["crop"], "Soybeans")
        self.assertEqual(a["crop"], "Corn")
        # The pond inside B becomes exactly one keepout hole; A has none.
        self.assertEqual(len(b["holes"]), 1)
        self.assertEqual(a["holes"], [])
        self.assertGreaterEqual(len(b["holes"][0]), 3)

    def test_rect_field_traces_to_four_corners(self):
        out = self._run(synth_grid())
        a = min(out["fields"], key=lambda f: f["acres"])   # corn rectangle
        self.assertEqual(len(a["polygon"]), 4)

    def test_boundary_hugs_pixel_edges(self):
        """Corner vertices must land on exact pixel-corner geo positions —
        the old center-tracing sat half a pixel (15 m) inside."""
        out = self._run(synth_grid())
        a = min(out["fields"], key=lambda f: f["acres"])
        ulx, uly = to_albers(39.92, -95.82)
        expected = []
        for cx, cy in ((4, 5), (16, 5), (16, 35), (4, 35)):  # A: cols 4..16, rows 5..35
            expected.append(from_albers(ulx + cx * 30.0, uly - cy * 30.0))
        self.assertEqual(len(a["polygon"]), 4)
        for elat, elon in expected:
            # within 30 cm of the exact pixel corner (rounding noise only)
            best = min(
                ((p["lat"] - elat) ** 2 + ((p["lon"] - elon) * 0.766) ** 2) ** 0.5 * 111320.0
                for p in a["polygon"])
            self.assertLess(best, 0.3)

    def test_selection_too_large_raises(self):
        sel = [{"lat": 39.9, "lon": -95.9}, {"lat": 40.2, "lon": -95.9},
               {"lat": 40.2, "lon": -95.5}, {"lat": 39.9, "lon": -95.5}]
        with self.assertRaises(ValueError):
            cdl.detect_fields_cdl(sel, year=2025)


if __name__ == "__main__":
    unittest.main()

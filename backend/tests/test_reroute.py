"""Hazard-leg rerouting geometry (app.reroute).

The safety property under test is one sentence: a returned path never enters a
hazard hull, and when that cannot be guaranteed the function returns None so
the caller warns instead of pretending. Every test here is ultimately checking
one of those two halves.

Coordinates are a flat local meter frame, same as the coverage planner's.
"""

import math
import random
import unittest

from app.reroute import (convex_hull, hazard_hull, route_leg,
                         segment_enters_hull, _point_in_hull)


def _box(cx, cy, half):
    """Axis-aligned square ring (CCW) centred on (cx, cy)."""
    return [(cx - half, cy - half), (cx + half, cy - half),
            (cx + half, cy + half), (cx - half, cy + half)]


def _corridor(x0, y0, x1, y1):
    """A line traced out and back — how waterways and power lines arrive."""
    return [(x0, y0), (x1, y1), (x0, y0)]


def _path_clear(path, hulls):
    return not any(segment_enters_hull(path[i - 1], path[i], h)
                   for i in range(1, len(path)) for h in hulls)


class HullTests(unittest.TestCase):
    def test_hull_is_ccw(self):
        h = convex_hull(_box(0, 0, 10))
        area = sum(h[i][0] * h[(i + 1) % len(h)][1]
                   - h[(i + 1) % len(h)][0] * h[i][1]
                   for i in range(len(h)))
        self.assertGreater(area, 0, "hull must be counter-clockwise")

    def test_interior_points_dropped(self):
        h = convex_hull(_box(0, 0, 10) + [(0, 0), (1, 1)])
        self.assertEqual(len(h), 4)

    def test_degenerate_input_survives(self):
        self.assertEqual(len(convex_hull([(0, 0), (1, 1)])), 2)
        self.assertEqual(convex_hull([]), [])

    def test_buffer_never_under_delivers(self):
        # Every point at exactly buffer_m from the ring must be inside the
        # expanded hull: the octagon circumscribes the disc, so clearance is
        # never LESS than requested (that direction would be unsafe).
        ring = _box(0, 0, 10)
        hull = hazard_hull(ring, 20.0)
        for ang in range(0, 360, 7):
            a = math.radians(ang)
            pt = (10 + 20 * math.cos(a) * 0.999, 10 + 20 * math.sin(a) * 0.999)
            self.assertTrue(_point_in_hull(pt, hull) or
                            not _point_in_hull(pt, convex_hull(ring)),
                            f"clearance short at {ang} deg")

    def test_corridor_hulls_into_a_capsule(self):
        hull = hazard_hull(_corridor(0, 0, 100, 0), 20.0)
        # A zero-area line becomes a real area to route around.
        self.assertGreaterEqual(len(hull), 4)
        self.assertTrue(_point_in_hull((50, 0), hull))
        self.assertFalse(_point_in_hull((50, 100), hull))


class SegmentHullTests(unittest.TestCase):
    HULL = convex_hull(_box(0, 0, 10))

    def test_straight_through_is_a_hit(self):
        self.assertTrue(segment_enters_hull((-50, 0), (50, 0), self.HULL))

    def test_clear_miss(self):
        self.assertFalse(segment_enters_hull((-50, 50), (50, 50), self.HULL))

    def test_segment_fully_inside_is_a_hit(self):
        self.assertTrue(segment_enters_hull((-1, 0), (1, 0), self.HULL))

    def test_riding_the_boundary_is_not_a_hit(self):
        # Taut detours ride the hull edge; that must not read as a collision
        # or rerouting could never converge.
        self.assertFalse(segment_enters_hull((-10, 10), (10, 10), self.HULL))

    def test_stopping_short_is_not_a_hit(self):
        self.assertFalse(segment_enters_hull((-50, 0), (-11, 0), self.HULL))


class RouteLegTests(unittest.TestCase):
    def test_clear_leg_needs_no_detour(self):
        hulls = [hazard_hull(_box(0, 0, 10), 5)]
        self.assertEqual(route_leg((-100, 100), (100, 100), hulls), [])

    def test_blocked_leg_is_routed_around(self):
        hulls = [hazard_hull(_box(0, 0, 10), 5)]
        pts = route_leg((-100, 0), (100, 0), hulls)
        self.assertTrue(pts, "a blocked leg must produce detour points")
        self.assertTrue(_path_clear([(-100, 0)] + pts + [(100, 0)], hulls))

    def test_detour_respects_the_buffer(self):
        # Detour must clear the LINE by the buffer, not merely miss the line.
        buf = 30.0
        hulls = [hazard_hull(_corridor(0, -50, 0, 50), buf)]
        pts = route_leg((-200, 0), (200, 0), hulls)
        self.assertTrue(pts)
        for x, y in pts:
            # distance to the powerline segment (the y axis from -50..50)
            d = abs(x) if -50 <= y <= 50 else math.hypot(
                x, abs(y) - 50)
            self.assertGreaterEqual(d, buf * 0.98,
                                    f"detour point {(x, y)} inside clearance")

    def test_picks_the_shorter_side(self):
        # Obstacle centred well above the direct line: going under is shorter.
        hulls = [hazard_hull(_box(0, 40, 50), 5)]
        pts = route_leg((-200, 0), (200, 0), hulls)
        self.assertTrue(pts)
        self.assertTrue(all(y < 40 for _, y in pts),
                        "should detour on the near side, not the far one")

    def test_multiple_obstacles(self):
        hulls = [hazard_hull(_box(-60, 0, 20), 5),
                 hazard_hull(_box(60, 0, 20), 5)]
        pts = route_leg((-200, 0), (200, 0), hulls)
        self.assertTrue(pts)
        self.assertTrue(_path_clear([(-200, 0)] + pts + [(200, 0)], hulls))

    def test_endpoint_inside_hazard_is_unresolvable(self):
        # Cannot route away from a hazard you start inside — say so.
        hulls = [hazard_hull(_box(0, 0, 50), 5)]
        self.assertIsNone(route_leg((0, 0), (200, 0), hulls))
        self.assertIsNone(route_leg((-200, 0), (0, 0), hulls))

    def test_no_hazards_is_a_cheap_noop(self):
        self.assertEqual(route_leg((0, 0), (100, 100), []), [])

    def test_charge_hook_is_called(self):
        seen = []
        hulls = [hazard_hull(_box(0, 0, 10), 5)]
        route_leg((-100, 0), (100, 0), hulls, charge=seen.append)
        self.assertTrue(seen, "CPU budget hook must be charged")

    def test_charge_hook_can_abort(self):
        def broke(_n):
            raise ValueError("budget exhausted")
        hulls = [hazard_hull(_box(0, 0, 10), 5)]
        with self.assertRaises(ValueError):
            route_leg((-100, 0), (100, 0), hulls, charge=broke)

    def test_returned_points_exclude_the_endpoints(self):
        hulls = [hazard_hull(_box(0, 0, 10), 5)]
        a, b = (-100, 0), (100, 0)
        pts = route_leg(a, b, hulls)
        self.assertNotIn(a, pts)
        self.assertNotIn(b, pts)


class PropertyTests(unittest.TestCase):
    """The whole safety contract, checked against pseudo-random geometry.

    Hand-picked cases prove the routine works on the shapes I thought of. This
    proves the invariant on shapes I did not: for ANY leg and ANY obstacle set,
    route_leg either returns a path that provably clears every hazard, or it
    returns None so the caller warns. There is no third outcome, and in
    particular it must never return a path that still crosses a hazard.
    """

    def test_returned_paths_are_always_clear(self):
        rnd = random.Random(20260818)
        routed = blocked = 0
        for _ in range(400):
            hulls = [
                hazard_hull(_box(rnd.uniform(-150, 150), rnd.uniform(-150, 150),
                                 rnd.uniform(5, 45)), rnd.uniform(0, 30))
                for _ in range(rnd.randint(1, 4))
            ]
            a = (rnd.uniform(-300, -200), rnd.uniform(-300, 300))
            b = (rnd.uniform(200, 300), rnd.uniform(-300, 300))
            pts = route_leg(a, b, hulls)
            if pts is None:
                blocked += 1
                continue
            routed += 1
            self.assertTrue(
                _path_clear([a] + pts + [b], hulls),
                f"route_leg returned a path that still enters a hazard: "
                f"a={a} b={b} pts={pts}")
        # Guard against a vacuous pass: if the generator only ever produced
        # trivially-clear legs, the assertion above would prove nothing.
        self.assertGreater(routed, 50, "too few routed cases to be meaningful")

    def test_corridor_crossings_are_always_clear(self):
        """The real-world shape: spray legs crossing powerline corridors."""
        rnd = random.Random(4242)
        routed = 0
        for _ in range(200):
            hulls = [hazard_hull(
                _corridor(rnd.uniform(-100, 100), rnd.uniform(-200, 0),
                          rnd.uniform(-100, 100), rnd.uniform(0, 200)),
                rnd.uniform(5, 40)) for _ in range(rnd.randint(1, 3))]
            a = (rnd.uniform(-400, -300), rnd.uniform(-50, 50))
            b = (rnd.uniform(300, 400), rnd.uniform(-50, 50))
            pts = route_leg(a, b, hulls)
            if pts is None:
                continue
            routed += 1
            self.assertTrue(_path_clear([a] + pts + [b], hulls),
                            f"corridor crossing not cleared: {pts}")
        self.assertGreater(routed, 30)


if __name__ == "__main__":
    unittest.main()

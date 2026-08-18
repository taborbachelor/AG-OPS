"""Live keepout-proximity monitor: geometry prep, distance, and the guardian
rule built on top of it (SPRAY-FLIGHT-SAFETY.md item 5).
"""
import math
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app import guardian, keepout_watch
from app.guardian import GuardianConfig, evaluate
from app.main import app
from app.vehicle_manager import VehicleManager, vehicle_manager

HOME_LAT, HOME_LON = 39.9042, -95.7997


def _offset(lat, lon, north_m, east_m):
    return (lat + north_m / 111320.0,
            lon + east_m / (111320.0 * math.cos(math.radians(lat))))


def _square(lat, lon, half_m=25.0):
    """A closed square ring centred on (lat, lon)."""
    pts = []
    for dn, de in ((-half_m, -half_m), (-half_m, half_m),
                   (half_m, half_m), (half_m, -half_m)):
        la, lo = _offset(lat, lon, dn, de)
        pts.append({"lat": la, "lon": lo})
    pts.append(dict(pts[0]))
    return pts


def _zones(**kinds):
    return {kind: [{"kind": kind, "coords": ring} for ring in rings]
            for kind, rings in kinds.items()}


class TestPrepare(unittest.TestCase):
    def test_accepts_the_coverage_response_shape(self):
        z = _zones(powerline=[_square(HOME_LAT, HOME_LON)],
                   water=[_square(HOME_LAT + 0.01, HOME_LON)])
        z["source"] = "overpass"          # the real response carries this
        z["zones_unavailable"] = False
        p = keepout_watch.prepare(z, hazard_buffer_m=20.0)
        self.assertEqual((p["n_hazards"], p["n_keepouts"]), (1, 1))

    def test_accepts_a_flat_list(self):
        p = keepout_watch.prepare(
            [{"kind": "powerline", "coords": _square(HOME_LAT, HOME_LON)}], 20.0)
        self.assertEqual(p["n_hazards"], 1)

    def test_only_powerlines_count_as_hazards(self):
        z = _zones(water=[_square(HOME_LAT, HOME_LON)],
                   trees=[_square(HOME_LAT, HOME_LON)],
                   buildings=[_square(HOME_LAT, HOME_LON)])
        p = keepout_watch.prepare(z, 20.0)
        self.assertEqual(p["n_hazards"], 0, "spray-quality zones are not hazards")
        self.assertEqual(p["n_keepouts"], 3)

    def test_malformed_geometry_is_refused_not_ignored(self):
        for bad in ([{"kind": "powerline", "coords": [{"lat": "x", "lon": 0}]}],
                    [{"kind": "powerline",
                      "coords": [{"lat": 91.0, "lon": 0}, {"lat": 0, "lon": 0},
                                 {"lat": 1, "lon": 1}]}]):
            with self.assertRaises(ValueError):
                keepout_watch.prepare(bad, 20.0)

    def test_degenerate_rings_are_dropped_quietly(self):
        p = keepout_watch.prepare(
            [{"kind": "powerline", "coords": [{"lat": 39.9, "lon": -95.8}]}], 20.0)
        self.assertEqual(p["rings"], [])

    def test_over_the_cap_hazards_are_kept_and_the_drop_is_reported(self):
        rings = [_square(HOME_LAT + i * 0.001, HOME_LON)
                 for i in range(keepout_watch.MAX_RINGS + 20)]
        z = _zones(water=rings, powerline=[_square(HOME_LAT, HOME_LON)])
        p = keepout_watch.prepare(z, 20.0)
        self.assertEqual(len(p["rings"]), keepout_watch.MAX_RINGS)
        self.assertEqual(p["dropped"], 21)
        self.assertEqual(p["n_hazards"], 1, "a hazard must never be the one dropped")
        self.assertFalse(p["complete"], "a truncated set must say so")

    def test_a_real_world_sized_query_is_not_truncated(self):
        """Sized from real OSM: a 3 km query over Topeka KS returns 3,679
        buildings. The cap exists for pathological input, not for a normal
        built-up area, and the old 400 would have dropped 90% of it."""
        self.assertGreaterEqual(keepout_watch.MAX_RINGS, 3700)

    def test_completeness_rides_through_to_the_proximity_result(self):
        rings = [_square(HOME_LAT + i * 0.001, HOME_LON)
                 for i in range(keepout_watch.MAX_RINGS + 5)]
        p = keepout_watch.prepare(_zones(water=rings), 20.0)
        out = keepout_watch.nearest(p, HOME_LAT, HOME_LON)
        self.assertFalse(out["keepout_complete"])


class TestNearest(unittest.TestCase):
    def setUp(self):
        self.prepared = keepout_watch.prepare(
            _zones(powerline=[_square(HOME_LAT, HOME_LON, half_m=25.0)],
                   water=[_square(*_offset(HOME_LAT, HOME_LON, 0, 500), half_m=25.0)]),
            hazard_buffer_m=20.0)

    def test_no_rings_means_unknown_not_clear(self):
        out = keepout_watch.nearest(keepout_watch.prepare([], 20.0), HOME_LAT, HOME_LON)
        self.assertFalse(out["known"])
        self.assertIsNone(out["hazard_dist_m"])
        self.assertFalse(out["breach"])

    def test_inside_a_hazard_is_zero_distance_and_a_breach(self):
        out = keepout_watch.nearest(self.prepared, HOME_LAT, HOME_LON)
        self.assertEqual(out["hazard_dist_m"], 0.0)
        self.assertTrue(out["breach"])
        self.assertEqual(out["hazard_kind"], "powerline")

    def test_just_outside_the_buffer_is_not_a_breach(self):
        lat, lon = _offset(HOME_LAT, HOME_LON, 0, 25.0 + 30.0)  # 30 m clear
        out = keepout_watch.nearest(self.prepared, lat, lon)
        self.assertAlmostEqual(out["hazard_dist_m"], 30.0, delta=1.0)
        self.assertFalse(out["breach"])

    def test_inside_the_buffer_but_outside_the_ring_is_a_breach(self):
        lat, lon = _offset(HOME_LAT, HOME_LON, 0, 25.0 + 10.0)  # 10 m clear
        out = keepout_watch.nearest(self.prepared, lat, lon)
        self.assertAlmostEqual(out["hazard_dist_m"], 10.0, delta=1.0)
        self.assertTrue(out["breach"], "10 m from a wire with a 20 m buffer")

    def test_water_distance_is_measured_but_never_a_breach(self):
        """Sitting inside the pond: reported, but not an airframe breach."""
        lat, lon = _offset(HOME_LAT, HOME_LON, 0, 500)
        out = keepout_watch.nearest(self.prepared, lat, lon)
        self.assertEqual(out["keepout_dist_m"], 0.0)
        self.assertFalse(out["breach"])

    def test_bbox_prefilter_agrees_with_the_unfiltered_answer(self):
        """The skip is an exact bound, so adding far-away rings must not
        change the reported minimum."""
        near = keepout_watch.nearest(self.prepared, *_offset(HOME_LAT, HOME_LON, 0, 60))
        many = _zones(
            powerline=[_square(HOME_LAT, HOME_LON, half_m=25.0)]
            + [_square(HOME_LAT + 0.02 * i, HOME_LON, half_m=25.0)
               for i in range(1, 40)])
        out = keepout_watch.nearest(keepout_watch.prepare(many, 20.0),
                                    *_offset(HOME_LAT, HOME_LON, 0, 60))
        self.assertAlmostEqual(near["hazard_dist_m"], out["hazard_dist_m"], delta=0.5)


def _telem(**over):
    t = {"armed": True, "mode": "AUTO", "altitude": 60.0, "airspeed": 18.0,
         "groundspeed": 18.0, "battery_voltage": 12.4, "gps_fix": 3,
         "gps_satellites": 10, "link_level": "good",
         "lat": HOME_LAT, "lon": HOME_LON}
    t.update(over)
    return t


class TestGuardianRule(unittest.TestCase):
    def test_no_zone_data_is_reported_as_unknown_not_ok(self):
        res = evaluate(GuardianConfig(), _telem(), guardian.default_memory(), 1000.0)
        mon = res["monitors"]["keepout"]
        self.assertFalse(mon["known"], "no data must not render as a green tick")
        self.assertEqual(res["warnings"], [])

    def test_breach_warns_and_names_the_hazard(self):
        t = _telem(keepout={"known": True, "hazard_dist_m": 8.0,
                            "hazard_kind": "powerline", "keepout_dist_m": None,
                            "breach": True, "buffer_m": 20.0})
        res = evaluate(GuardianConfig(), t, guardian.default_memory(), 1000.0)
        joined = " ".join(res["warnings"])
        self.assertIn("powerline", joined)
        self.assertIn("8 m away", joined)
        self.assertIsNone(res["action"], "keepout proximity is warn-only by default")

    def test_breach_can_rtl_only_when_explicitly_configured(self):
        cfg = GuardianConfig(keepout_action="rtl")
        t = _telem(keepout={"known": True, "hazard_dist_m": 5.0,
                            "hazard_kind": "powerline", "breach": True,
                            "buffer_m": 20.0})
        res = evaluate(cfg, t, guardian.default_memory(), 1000.0)
        self.assertEqual((res["action"], res["source"]), ("rtl", "keepout"))

    def test_disarmed_breach_is_not_an_event(self):
        """A vehicle parked in the truck next to a pole is not a breach."""
        t = _telem(armed=False,
                   keepout={"known": True, "hazard_dist_m": 2.0,
                            "hazard_kind": "powerline", "breach": True,
                            "buffer_m": 20.0})
        res = evaluate(GuardianConfig(), t, guardian.default_memory(), 1000.0)
        self.assertTrue(res["monitors"]["keepout"]["ok"])


class TestKeepoutApi(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        vehicle_manager.clear_mission_keepouts()

    def tearDown(self):
        vehicle_manager.clear_mission_keepouts()

    def test_load_then_status_then_clear(self):
        z = _zones(powerline=[_square(HOME_LAT, HOME_LON)],
                   water=[_square(HOME_LAT + 0.01, HOME_LON)])
        r = self.client.post("/api/safety/keepouts",
                             json={"zones": z, "hazard_buffer_m": 25.0})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["hazards"], r.json()["keepouts"]), (1, 1))

        status = self.client.get("/api/safety/keepouts").json()
        self.assertTrue(status["known"])
        self.assertEqual(status["hazard_buffer_m"], 25.0)

        self.client.delete("/api/safety/keepouts")
        self.assertFalse(self.client.get("/api/safety/keepouts").json()["known"])

    def test_malformed_zones_are_a_422(self):
        r = self.client.post("/api/safety/keepouts",
                             json={"zones": [{"kind": "powerline",
                                              "coords": [{"lat": "nope", "lon": 0}]}]})
        self.assertEqual(r.status_code, 422)

    def test_mission_upload_clears_stale_rings(self):
        """The core fail-safe: rings from the previous field must never be
        silently reused against a new mission."""
        self.client.post("/api/safety/keepouts",
                         json={"zones": _zones(
                             powerline=[_square(HOME_LAT, HOME_LON)])})
        self.assertTrue(self.client.get("/api/safety/keepouts").json()["known"])

        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "upload_mission",
                               return_value={"ok": True, "count": 1}):
            r = self.client.post("/api/mission/upload", json={"items": [
                {"command": "WAYPOINT", "lat": 39.9, "lon": -95.8, "alt": 50}]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(self.client.get("/api/safety/keepouts").json()["known"],
                         "a new mission must reset proximity to unknown")


if __name__ == "__main__":
    unittest.main()

"""M6 pre-flight gate unit tests: evaluation rules + the arm/takeoff gate."""
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app import preflight
from app.main import app
from app.vehicle_manager import vehicle_manager


def _ready_snapshot(**over):
    t = {"connected": True, "link_state": "READY", "gps_fix": 3,
         "gps_satellites": 10, "ekf_healthy": True, "ekf_flags": 831,
         "home_lat": 39.9042, "home_lon": -95.7997, "lat": 39.9042,
         "lon": -95.7997, "battery_voltage": 12.4, "battery_level": 95,
         "rc_channels": [1500] * 8, "sensor_errors": []}
    t.update(over)
    return t


class TestEvaluate(unittest.TestCase):
    def test_ready_vehicle_is_go(self):
        pf = preflight.evaluate(_ready_snapshot(), fence_enable=1)
        self.assertTrue(pf["ready"])
        self.assertEqual(pf["failed_blockers"], [])
        self.assertEqual(pf["advisories_failing"], [])

    def test_each_blocker_blocks(self):
        cases = {
            "link": {"link_state": "DEGRADED"},
            "gps": {"gps_fix": 1},
            "ekf": {"ekf_healthy": False},
            "home": {"home_lat": 0.0, "lat": 0.0},
        }
        for cid, over in cases.items():
            pf = preflight.evaluate(_ready_snapshot(**over), fence_enable=1)
            self.assertFalse(pf["ready"], cid)
            self.assertIn(cid, pf["failed_blockers"])

    def test_advisories_inform_but_never_block(self):
        pf = preflight.evaluate(
            _ready_snapshot(battery_level=10, rc_channels=[],
                            sensor_errors=["mag"]),
            fence_enable=None)
        self.assertTrue(pf["ready"], "advisories must not block")
        self.assertEqual(set(pf["advisories_failing"]),
                         {"battery", "rc", "fence", "sensors"})

    def test_disconnected_is_no_go(self):
        pf = preflight.evaluate({"connected": False}, None)
        self.assertFalse(pf["ready"])
        self.assertIn("link", pf["failed_blockers"])


class TestGate(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_preflight_endpoint_shape(self):
        r = self.client.get("/api/safety/preflight")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("ready", body)
        self.assertEqual({c["id"] for c in body["checks"]},
                         {"link", "gps", "ekf", "home", "battery", "rc",
                          "fence", "sensors"})

    def test_arm_blocked_when_not_ready(self):
        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "snapshot",
                               return_value=_ready_snapshot(gps_fix=0)), \
             mock.patch.object(vehicle_manager, "cached_value",
                               return_value=1), \
             mock.patch.object(vehicle_manager, "arm") as arm:
            r = self.client.post("/api/vehicle/arm", json={"force": True})
        self.assertEqual(r.status_code, 409)
        arm.assert_not_called()
        self.assertTrue(any("GPS" in f for f in r.json()["detail"]["failed"]))

    def test_arm_override_is_honored_and_explicit(self):
        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "snapshot",
                               return_value=_ready_snapshot(gps_fix=0)), \
             mock.patch.object(vehicle_manager, "cached_value",
                               return_value=1), \
             mock.patch.object(vehicle_manager, "arm",
                               return_value={"ok": True, "result": 0,
                                             "error": None}) as arm:
            r = self.client.post("/api/vehicle/arm",
                                 json={"force": True, "override": True})
        self.assertEqual(r.status_code, 200)
        arm.assert_called_once()

    def test_ready_vehicle_arms_without_override(self):
        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "snapshot",
                               return_value=_ready_snapshot()), \
             mock.patch.object(vehicle_manager, "cached_value",
                               return_value=1), \
             mock.patch.object(vehicle_manager, "arm",
                               return_value={"ok": True, "result": 0,
                                             "error": None}):
            r = self.client.post("/api/vehicle/arm", json={"force": True})
        self.assertEqual(r.status_code, 200)

    def test_takeoff_gated_too(self):
        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "snapshot",
                               return_value=_ready_snapshot(ekf_healthy=False)), \
             mock.patch.object(vehicle_manager, "cached_value",
                               return_value=1), \
             mock.patch.object(vehicle_manager, "takeoff") as tko:
            r = self.client.post("/api/vehicle/takeoff",
                                 json={"alt": 50, "force": True})
        self.assertEqual(r.status_code, 409)
        tko.assert_not_called()


if __name__ == "__main__":
    unittest.main()

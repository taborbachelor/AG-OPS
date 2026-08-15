"""Tests for the pre-first-flight hardening pass (backend audit fixes):

- guardian stands down from RTL during an active landing approach
- a stale landing latch clears when the operator abandons the landing
- land() refuses without a real HOME_POSITION
- full param sync / restore are refused while armed
- snapshot() is strict-JSON safe even with NaN telemetry
- log_event can never raise into a flight-critical caller
"""
import json
import math
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app import eventlog, guardian
from app.guardian import GuardianConfig
from app.main import app
from app.vehicle_manager import VehicleManager, vehicle_manager


class TestGuardianLandingStanddown(unittest.TestCase):
    def _vm(self):
        vm = VehicleManager()
        vm.guardian_config = GuardianConfig(batt_low_s=0.0)
        vm.telemetry.armed = True
        vm.telemetry.mode = "AUTO"
        vm.telemetry.gps_fix = 3
        vm.telemetry.gps_satellites = 10
        vm.telemetry.battery_voltage = 12.4
        return vm

    def test_rtl_suppressed_during_landing_approach(self):
        """Battery-low during a land() approach must NOT abort the landing
        into a climb to RTL altitude on a dying pack."""
        vm = self._vm()
        vm.set_mode = mock.Mock(return_value=True)
        vm._guardian_tick(1000.0)              # arms the rising edge
        vm._landing_requested = True           # land() flow active
        vm.telemetry.battery_voltage = 10.0    # sustained low (batt_low_s=0)
        vm._guardian_tick(1001.0)
        vm._guardian_tick(1002.0)
        vm.set_mode.assert_not_called()
        # Warnings still flow — suppression is action-only, not blindness.
        self.assertTrue(vm.guardian_state()["warnings"])
        # State machine still reports the landing.
        self.assertEqual(vm.guardian_state()["state"], guardian.LANDING)

    def test_rtl_fires_after_landing_aborted(self):
        vm = self._vm()
        vm.set_mode = mock.Mock(return_value=True)
        vm._guardian_tick(1000.0)
        vm._landing_requested = True
        vm.telemetry.battery_voltage = 10.0
        vm._guardian_tick(1001.0)              # suppressed (landing, AUTO seen)
        vm.set_mode.assert_not_called()
        vm.telemetry.mode = "LOITER"           # operator abandons the landing
        vm._guardian_tick(1002.0)              # latch clears
        self.assertFalse(vm._landing_requested)
        vm._guardian_tick(1003.0)              # condition persists -> RTL now
        vm.set_mode.assert_called_with("RTL")

    def test_stale_landing_latch_clears_on_mode_exit(self):
        vm = self._vm()
        vm._guardian_tick(1000.0)
        vm._landing_requested = True
        vm._guardian_tick(1001.0)              # AUTO observed
        self.assertTrue(vm._landing_seen_auto)
        vm.telemetry.mode = "LOITER"
        vm._guardian_tick(1002.0)
        self.assertFalse(vm._landing_requested,
                         "aborted landing must not label later AUTO as LANDING")


class TestLandRequiresHome(unittest.TestCase):
    def test_land_refuses_without_home_position(self):
        vm = VehicleManager()
        vm.connection = mock.Mock()
        vm.telemetry.lat, vm.telemetry.lon = 39.91, -95.80  # position known
        res = vm.land()
        self.assertFalse(res["ok"])
        self.assertIn("home position", res["error"])
        self.assertIn("RTL", res["error"])


class TestArmedGates(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def _armed(self):
        snap = dict(vehicle_manager.snapshot())
        snap["armed"] = True
        return mock.patch.object(vehicle_manager, "snapshot", return_value=snap)

    def test_full_param_download_refused_while_armed(self):
        with mock.patch.object(vehicle_manager, "connected", True), self._armed():
            r = self.client.get("/api/vehicle/params")
        self.assertEqual(r.status_code, 409)

    def test_cached_params_still_served_while_armed(self):
        with mock.patch.object(vehicle_manager, "connected", True), self._armed():
            r = self.client.get("/api/vehicle/params?cached=true")
        self.assertEqual(r.status_code, 200)

    def test_background_sync_refused_while_armed(self):
        with mock.patch.object(vehicle_manager, "connected", True), self._armed():
            r = self.client.post("/api/vehicle/params/sync")
        self.assertEqual(r.status_code, 409)

    def test_param_restore_refused_while_armed(self):
        with mock.patch.object(vehicle_manager, "connected", True), self._armed():
            r = self.client.post("/api/vehicle/params/restore",
                                 json={"content": "WP_RADIUS,45"})
        self.assertEqual(r.status_code, 409)


class TestSnapshotJsonSafety(unittest.TestCase):
    def test_nan_telemetry_serializes_to_strict_json(self):
        vm = VehicleManager()
        vm.telemetry.airspeed = float("nan")
        vm.telemetry.groundspeed = float("inf")
        snap = vm.snapshot()
        # Must not raise, and must not contain bare NaN/Infinity literals.
        text = json.dumps(snap, allow_nan=False)
        self.assertIsNone(snap["airspeed"])
        self.assertIsNone(snap["groundspeed"])
        self.assertNotIn("NaN", text)


class TestEventlogNeverRaises(unittest.TestCase):
    def test_deeply_nested_value(self):
        deep = []
        cur = deep
        for _ in range(10000):
            nxt = []
            cur.append(nxt)
            cur = nxt
        eventlog.log_event("test", "deep_value", payload=deep)  # must not raise

    def test_broken_repr(self):
        class Bad:
            def __repr__(self):
                raise RuntimeError("broken repr")
        eventlog.log_event("test", "bad_repr", payload=Bad())  # must not raise

    def test_core_fields_not_clobbered(self):
        # `t` and `ts` are the core fields a caller can actually collide with
        # via **fields (component/event/level are positional parameter names).
        eventlog.log_event("test", "clobber", t="not-a-time", ts="fake-ts")
        rec = eventlog.recent_events(1)[0]
        self.assertEqual(rec["component"], "test")
        self.assertEqual(rec["event"], "clobber")
        self.assertIsInstance(rec["t"], float)
        self.assertEqual(rec["f_t"], "not-a-time")
        self.assertEqual(rec["f_ts"], "fake-ts")

    def test_nan_field_is_stringified(self):
        eventlog.log_event("test", "nan_field", value=float("nan"))
        rec = eventlog.recent_events(1)[0]
        self.assertIsInstance(rec["value"], str)
        self.assertTrue(math.isnan(float(rec["value"])))


if __name__ == "__main__":
    unittest.main()

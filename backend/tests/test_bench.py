"""Bench & first-flight kit unit tests: servo guard, calibration mapping,
first-flight bundle math, param backup/restore parsing."""
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from pymavlink import mavutil

from app.main import app
from app.routers import bench
from app.vehicle_manager import vehicle_manager

OK = {"ok": True, "result": 0, "error": None}


def _snapshot(armed=False, **over):
    d = {"armed": armed, "param_sync": {"synced": True},
         "capabilities": {"fw_version": "4.8.0"}}
    d.update(over)
    return d


class TestServo(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_requires_connection_and_disarmed(self):
        with mock.patch.object(vehicle_manager, "connected", False):
            r = self.client.post("/api/bench/servo",
                                 json={"channel": 5, "pwm": 1500})
        self.assertEqual(r.status_code, 400)
        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "snapshot",
                               return_value=_snapshot(armed=True)):
            r = self.client.post("/api/bench/servo",
                                 json={"channel": 5, "pwm": 1500})
        self.assertEqual(r.status_code, 409)

    def test_assigned_channels_refused_aux_channels_driven(self):
        def cached(name):
            return {"SERVO1_FUNCTION": 4, "SERVO5_FUNCTION": 0}.get(name)
        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "snapshot",
                               return_value=_snapshot()), \
             mock.patch.object(vehicle_manager, "cached_value",
                               side_effect=cached), \
             mock.patch.object(vehicle_manager, "run_command",
                               return_value=OK) as rc:
            r = self.client.post("/api/bench/servo",
                                 json={"channel": 1, "pwm": 1900})
            self.assertEqual(r.status_code, 409,
                             "autopilot-owned channel must be refused")
            self.assertIn("surface", r.json()["detail"]["hint"])
            rc.assert_not_called()
            r = self.client.post("/api/bench/servo",
                                 json={"channel": 5, "pwm": 1700})
            self.assertEqual(r.status_code, 200)
            rc.assert_called_once()

    def test_pwm_bounds(self):
        r = self.client.post("/api/bench/servo", json={"channel": 5, "pwm": 3000})
        self.assertEqual(r.status_code, 422)


class TestSurface(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def _patches(self, set_mode_ok=True):
        return (mock.patch.object(vehicle_manager, "connected", True),
                mock.patch.object(vehicle_manager, "snapshot",
                                  return_value=_snapshot(
                                      servo_outputs=[1900] + [1500] * 7)),
                mock.patch.object(vehicle_manager, "cached_value",
                                  return_value=None),
                mock.patch.object(vehicle_manager, "set_mode",
                                  return_value=set_mode_ok),
                mock.patch.object(vehicle_manager, "send_rc_override",
                                  return_value=True),
                mock.patch.object(vehicle_manager, "release_rc_override"))

    def test_streams_override_then_always_releases(self):
        p = self._patches()
        with p[0], p[1], p[2], p[3], p[4] as send, p[5] as release:
            r = self.client.post("/api/bench/surface",
                                 json={"surface": "aileron", "pwm": 1900,
                                       "hold_s": 0.5})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["rc_channel"], 1)  # RCMAP_ROLL default
        self.assertEqual(body["servo_outputs_during_hold"][0], 1900)
        self.assertGreater(send.call_count, 5, "must stream at ~20Hz")
        self.assertEqual(send.call_args.args[0][0], 1900)
        release.assert_called_once()

    def test_throttle_needs_explicit_consent(self):
        p = self._patches()
        with p[0], p[1], p[2], p[3], p[4], p[5]:
            r = self.client.post("/api/bench/surface",
                                 json={"surface": "throttle", "pwm": 1200,
                                       "hold_s": 0.5})
            self.assertEqual(r.status_code, 409)
            r = self.client.post("/api/bench/surface",
                                 json={"surface": "throttle", "pwm": 1200,
                                       "hold_s": 0.5, "allow_throttle": True})
            self.assertEqual(r.status_code, 200)

    def test_released_even_when_stream_fails(self):
        p = self._patches()
        with p[0], p[1], p[2], p[3], \
             mock.patch.object(vehicle_manager, "send_rc_override",
                               return_value=False), \
             p[5] as release:
            r = self.client.post("/api/bench/surface",
                                 json={"surface": "rudder", "pwm": 1800,
                                       "hold_s": 0.5})
        self.assertEqual(r.status_code, 502)
        release.assert_called_once()

    def test_refused_while_armed(self):
        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "snapshot",
                               return_value=_snapshot(armed=True)):
            r = self.client.post("/api/bench/surface",
                                 json={"surface": "aileron", "pwm": 1900})
        self.assertEqual(r.status_code, 409)


class TestCalibrate(unittest.TestCase):
    def test_kind_maps_to_the_right_command(self):
        client = TestClient(app)
        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "snapshot",
                               return_value=_snapshot()), \
             mock.patch.object(vehicle_manager, "run_command",
                               return_value=OK) as rc:
            r = client.post("/api/bench/calibrate", json={"kind": "gyro"})
            self.assertEqual(r.status_code, 200)
            args = rc.call_args_list[0].args
            self.assertEqual(args[0],
                             mavutil.mavlink.MAV_CMD_PREFLIGHT_CALIBRATION)
            self.assertEqual(args[1], 1)  # p1 = gyro
            client.post("/api/bench/calibrate", json={"kind": "mag_start"})
            self.assertEqual(rc.call_args_list[1].args[0],
                             mavutil.mavlink.MAV_CMD_DO_START_MAG_CAL)


class TestFirstFlightBundle(unittest.TestCase):
    def test_preview_needs_no_vehicle_and_scales_by_cells(self):
        client = TestClient(app)
        r = client.post("/api/bench/first-flight-params",
                        json={"cells": 4, "apply": False})
        self.assertEqual(r.status_code, 200)
        p = r.json()["params"]
        self.assertAlmostEqual(p["BATT_LOW_VOLT"], 14.0)
        self.assertAlmostEqual(p["BATT_CRT_VOLT"], 13.2)
        self.assertEqual(p["FENCE_ENABLE"], 1)
        # No vehicle -> new-generation names assumed (RTL_ALTITUDE in meters).
        self.assertEqual(p["RTL_ALTITUDE"], 60.0)
        self.assertEqual(p["ARMING_SKIPCHK"], 0)
        g = r.json()["guardian"]
        self.assertGreater(g["batt_rtl_volt"], p["BATT_LOW_VOLT"],
                           "guardian must act before the autopilot")

    def test_bundle_uses_legacy_names_when_firmware_has_them(self):
        req = bench.FirstFlightRequest(cells=3)
        p = bench._first_flight_params(
            req, cached={"ARMING_CHECK": 1.0, "ALT_HOLD_RTL": 10000.0})
        self.assertEqual(p["ARMING_CHECK"], 1)
        self.assertEqual(p["ALT_HOLD_RTL"], 6000)  # cm
        self.assertNotIn("RTL_ALTITUDE", p)
        self.assertNotIn("ARMING_SKIPCHK", p)

    def test_apply_is_atomic_and_updates_guardian(self):
        client = TestClient(app)
        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "snapshot",
                               return_value=_snapshot()), \
             mock.patch.object(vehicle_manager, "set_params_atomic",
                               return_value={"ok": True}) as spa, \
             mock.patch.object(vehicle_manager, "set_guardian_config",
                               return_value={}) as sgc:
            r = client.post("/api/bench/first-flight-params",
                            json={"cells": 3, "apply": True})
        self.assertEqual(r.status_code, 200)
        spa.assert_called_once()
        self.assertAlmostEqual(sgc.call_args.args[0]["batt_rtl_volt"], 10.65)

    def test_apply_failure_is_loud(self):
        client = TestClient(app)
        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "snapshot",
                               return_value=_snapshot()), \
             mock.patch.object(vehicle_manager, "set_params_atomic",
                               return_value={"ok": False,
                                             "failed": "FENCE_RADIUS",
                                             "rolled_back": ["FENCE_TYPE"]}):
            r = client.post("/api/bench/first-flight-params",
                            json={"apply": True})
        self.assertEqual(r.status_code, 502)


class TestBackupRestore(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_backup_refuses_partial_cache(self):
        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "get_cached_params",
                               return_value={"A": 1.0}), \
             mock.patch.object(vehicle_manager, "snapshot",
                               return_value=_snapshot(
                                   param_sync={"synced": False})):
            r = self.client.get("/api/vehicle/params/backup")
        self.assertEqual(r.status_code, 409)

    def test_backup_is_mission_planner_format(self):
        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "get_cached_params",
                               return_value={"WP_RADIUS": 30.0,
                                             "FENCE_ENABLE": 1.0}), \
             mock.patch.object(vehicle_manager, "snapshot",
                               return_value=_snapshot()):
            r = self.client.get("/api/vehicle/params/backup")
        self.assertEqual(r.status_code, 200)
        lines = r.text.strip().splitlines()
        self.assertTrue(lines[0].startswith("#"))
        self.assertIn("FENCE_ENABLE,1", lines[1])
        self.assertIn("WP_RADIUS,30", lines[2])

    def test_restore_writes_only_diffs_and_reports_everything(self):
        cache = {"WP_RADIUS": 30.0, "FENCE_ENABLE": 1.0}
        writes = []

        def fake_set(name, value):
            writes.append((name, value))
            return {"verified": name != "FENCE_ENABLE", "accepted": value,
                    "requested": value}

        content = ("# comment\n"
                   "WP_RADIUS,90\n"          # differs -> written
                   "FENCE_ENABLE,0\n"        # differs -> fails verification
                   "WP_RADIUS_EXTRA\n"       # malformed
                   "NOT_ON_VEHICLE,5\n")     # unknown
        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "get_cached_params",
                               return_value=cache), \
             mock.patch.object(vehicle_manager, "set_param",
                               side_effect=fake_set):
            r = self.client.post("/api/vehicle/params/restore",
                                 json={"content": content})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["written"], ["WP_RADIUS"])
        self.assertEqual(body["failed"], ["FENCE_ENABLE"])
        self.assertEqual(body["unknown_on_vehicle"], ["NOT_ON_VEHICLE"])
        self.assertEqual(body["malformed_lines"], ["WP_RADIUS_EXTRA"])
        self.assertEqual(body["status"], "partial")

    def test_restore_skips_identical_values(self):
        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "get_cached_params",
                               return_value={"WP_RADIUS": 30.0}), \
             mock.patch.object(vehicle_manager, "set_param") as sp:
            r = self.client.post("/api/vehicle/params/restore",
                                 json={"content": "WP_RADIUS,30\n"})
        self.assertEqual(r.status_code, 200)
        sp.assert_not_called()
        self.assertEqual(r.json()["skipped_same"], 1)


if __name__ == "__main__":
    unittest.main()

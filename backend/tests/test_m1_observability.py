"""M1 observability regression suite: structured event log, STATUSTEXT capture,
EKF/sensor health interpretation, link quality, and the thread-safe snapshot.

Drives VehicleManager._handle_msg directly with fake MAVLink messages — no live
vehicle needed (same approach as test_flight_fixes.py).
"""
import time
import unittest
from types import SimpleNamespace

from pymavlink import mavutil

from app import eventlog
from app.vehicle_manager import (
    VehicleManager, _EKF_ATTITUDE, _EKF_POS_HORIZ_ABS, _EKF_POS_HORIZ_REL,
    _EKF_CONST_POS_MODE, _EKF_UNINITIALIZED, _SENSOR_BITS,
)


def msg(mtype, **fields):
    m = SimpleNamespace(**fields)
    m.get_type = lambda: mtype
    return m


def make_vm():
    vm = VehicleManager()
    vm._maybe_log = lambda: None  # don't open flight-log files in unit tests
    return vm


def last_event(component=None, event=None):
    for e in eventlog.recent_events(500):
        if (component is None or e["component"] == component) and \
           (event is None or e["event"] == event):
            return e
    return None


class TestEventLog(unittest.TestCase):
    def test_ring_and_fields(self):
        eventlog.log_event("testcomp", "hello", level="WARN", n=7, s="x")
        e = last_event("testcomp", "hello")
        self.assertIsNotNone(e)
        self.assertEqual(e["level"], "WARN")
        self.assertEqual(e["n"], 7)
        self.assertIn("ts", e)

    def test_unserializable_values_stringified_not_dropped(self):
        eventlog.log_event("testcomp", "weird", obj=object())
        e = last_event("testcomp", "weird")
        self.assertIsNotNone(e)
        self.assertIsInstance(e["obj"], str)

    def test_never_raises(self):
        # A hostile fields dict must not take down the caller.
        eventlog.log_event("testcomp", "hostile", **{"k": {1: object()}})


class TestStatustext(unittest.TestCase):
    def test_capture_and_snapshot(self):
        vm = make_vm()
        vm._handle_msg(msg("STATUSTEXT", severity=2, text=b"PreArm: Fence enabled\x00"))
        snap = vm.snapshot()
        self.assertEqual(len(snap["statustext"]), 1)
        entry = snap["statustext"][0]
        self.assertEqual(entry["text"], "PreArm: Fence enabled")
        self.assertEqual(entry["severity_name"], "CRITICAL")

    def test_event_logged_with_severity_level(self):
        vm = make_vm()
        vm._handle_msg(msg("STATUSTEXT", severity=3, text="Baro: cal failed"))
        e = last_event("vehicle", "statustext")
        self.assertIsNotNone(e)
        self.assertEqual(e["level"], "ERROR")
        self.assertEqual(e["text"], "Baro: cal failed")

    def test_ring_bounded_and_snapshot_last5(self):
        vm = make_vm()
        for i in range(60):
            vm._handle_msg(msg("STATUSTEXT", severity=6, text=f"msg {i}"))
        snap = vm.snapshot()
        self.assertEqual(len(snap["statustext"]), 5)
        self.assertEqual(snap["statustext"][-1]["text"], "msg 59")
        self.assertEqual(len(vm._statustext), 50)  # deque maxlen


class TestEkfHealth(unittest.TestCase):
    def test_healthy(self):
        vm = make_vm()
        flags = _EKF_ATTITUDE | _EKF_POS_HORIZ_ABS | _EKF_POS_HORIZ_REL
        vm._handle_msg(msg("EKF_STATUS_REPORT", flags=flags))
        self.assertTrue(vm.telemetry.ekf_healthy)
        self.assertEqual(vm.telemetry.ekf_flags, flags)

    def test_const_pos_mode_unhealthy(self):
        vm = make_vm()
        vm._handle_msg(msg("EKF_STATUS_REPORT",
                           flags=_EKF_ATTITUDE | _EKF_POS_HORIZ_ABS | _EKF_CONST_POS_MODE))
        self.assertFalse(vm.telemetry.ekf_healthy)

    def test_uninitialized_unhealthy(self):
        vm = make_vm()
        vm._handle_msg(msg("EKF_STATUS_REPORT",
                           flags=_EKF_ATTITUDE | _EKF_POS_HORIZ_ABS | _EKF_UNINITIALIZED))
        self.assertFalse(vm.telemetry.ekf_healthy)

    def test_no_position_solution_unhealthy(self):
        vm = make_vm()
        vm._handle_msg(msg("EKF_STATUS_REPORT", flags=_EKF_ATTITUDE))
        self.assertFalse(vm.telemetry.ekf_healthy)


class TestSensorHealth(unittest.TestCase):
    def _sys_status(self, enabled, health):
        return msg("SYS_STATUS", voltage_battery=12600, current_battery=150,
                   battery_remaining=88,
                   onboard_control_sensors_enabled=enabled,
                   onboard_control_sensors_health=health)

    def test_enabled_but_unhealthy_reported_by_name(self):
        vm = make_vm()
        enabled = _SENSOR_BITS["gyro"] | _SENSOR_BITS["gps"]
        health = _SENSOR_BITS["gyro"]  # gps enabled but unhealthy
        vm._handle_msg(self._sys_status(enabled, health))
        self.assertEqual(vm.telemetry.sensor_errors, ["gps"])
        self.assertAlmostEqual(vm.telemetry.battery_voltage, 12.6)

    def test_disabled_sensor_not_reported(self):
        vm = make_vm()
        vm._handle_msg(self._sys_status(_SENSOR_BITS["gyro"], _SENSOR_BITS["gyro"]))
        self.assertEqual(vm.telemetry.sensor_errors, [])

    def test_health_transition_logged_once(self):
        vm = make_vm()
        bad = self._sys_status(_SENSOR_BITS["mag"], 0)
        vm._handle_msg(bad)
        e = last_event("vehicle", "sensor_health")
        self.assertIsNotNone(e)
        self.assertEqual(e["unhealthy"], ["mag"])
        self.assertEqual(e["level"], "WARN")


class TestHeartbeatEvents(unittest.TestCase):
    def _hb(self, armed=False, custom_mode=0):
        base = mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED
        if armed:
            base |= mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
        return msg("HEARTBEAT", type=mavutil.mavlink.MAV_TYPE_FIXED_WING,
                   autopilot=mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA,
                   base_mode=base, custom_mode=custom_mode, mavlink_version=3)

    def test_armed_transition_logged(self):
        vm = make_vm()
        vm._handle_msg(self._hb(armed=False))
        vm._handle_msg(self._hb(armed=True))
        e = last_event("vehicle", "armed")
        self.assertIsNotNone(e)
        self.assertEqual(e["level"], "WARN")
        self.assertTrue(vm.telemetry.armed)

    def test_heartbeat_feeds_link_quality_window(self):
        vm = make_vm()
        vm._handle_msg(self._hb())
        self.assertEqual(len(vm._hb_times), 1)


class TestSnapshot(unittest.TestCase):
    def test_disconnected_shape(self):
        vm = make_vm()
        snap = vm.snapshot()
        self.assertFalse(snap["connected"])
        self.assertIsNone(snap["link_quality"])
        self.assertIsNone(snap["last_heartbeat_age"])
        self.assertEqual(snap["statustext"], [])
        # dataclass fields all present
        for key in ("armed", "mode", "gps_fix", "ekf_healthy", "sensor_errors"):
            self.assertIn(key, snap)

    def test_snapshot_is_a_copy(self):
        vm = make_vm()
        snap = vm.snapshot()
        snap["rc_channels"].append(1500)
        self.assertEqual(vm.telemetry.rc_channels, [])

    def test_link_quality_full_and_half(self):
        vm = make_vm()
        vm.connected = True
        now = time.time()
        vm._connected_at = now - 60
        vm._last_heartbeat = now
        vm._hb_times.extend(now - i for i in range(10))  # 10 hbs in last 10 s
        self.assertEqual(vm.snapshot()["link_quality"], 100)
        vm._hb_times.clear()
        vm._hb_times.extend(now - i for i in range(5))   # 5 hbs in last 10 s
        self.assertEqual(vm.snapshot()["link_quality"], 50)


class TestEndpoints(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from app.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_get_telemetry_includes_link_health(self):
        r = self.client.get("/api/telemetry/")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        for key in ("connected", "link_quality", "last_heartbeat_age",
                    "statustext", "ekf_healthy", "sensor_errors"):
            self.assertIn(key, body)

    def test_events_endpoint(self):
        eventlog.log_event("testcomp", "endpoint_probe")
        r = self.client.get("/api/logs/events?limit=10")
        self.assertEqual(r.status_code, 200)
        events = r.json()["events"]
        self.assertLessEqual(len(events), 10)
        self.assertTrue(any(e["event"] == "endpoint_probe" for e in events))

    def test_events_not_captured_by_log_name_route(self):
        # /api/logs/events must hit the events route, not /{name} validation.
        r = self.client.get("/api/logs/events")
        self.assertEqual(r.status_code, 200)
        self.assertIn("events", r.json())


if __name__ == "__main__":
    unittest.main()

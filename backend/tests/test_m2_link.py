"""M2 regression suite: link identity & connection state machine.

Covers, with no live vehicle:
  - RX sysid filtering (drop traffic from a co-connected GCS / other vehicle)
  - connection state machine transitions + logged events
  - graded link levels from heartbeat age
  - AUTOPILOT_VERSION capability decoding
  - BATTERY_STATUS consumed/remaining
  - snapshot exposes link_state / link_level / sysids / capabilities
"""
import time
import unittest
from types import SimpleNamespace

from pymavlink import mavutil

from app import eventlog
from app.vehicle_manager import VehicleManager, LinkState, _CAP_BITS


def msg(mtype, src=1, **fields):
    m = SimpleNamespace(**fields)
    m.get_type = lambda: mtype
    m.get_srcSystem = lambda: src
    return m


def make_vm():
    vm = VehicleManager()
    vm._maybe_log = lambda: None
    return vm


def last_event(component=None, event=None):
    for e in eventlog.recent_events(500):
        if (component is None or e["component"] == component) and \
           (event is None or e["event"] == event):
            return e
    return None


class RxFilterTests(unittest.TestCase):
    def test_accepts_everything_before_vehicle_locked(self):
        vm = make_vm()
        self.assertIsNone(vm._vehicle_sysid)
        self.assertTrue(vm._from_vehicle(msg("HEARTBEAT", src=42)))

    def test_rejects_other_system_once_locked(self):
        vm = make_vm()
        vm._vehicle_sysid = 1
        self.assertTrue(vm._from_vehicle(msg("HEARTBEAT", src=1)))
        self.assertFalse(vm._from_vehicle(msg("HEARTBEAT", src=255)))  # co-GCS

    def test_recv_blocking_ignores_foreign_heartbeat_for_watchdog(self):
        # A co-connected GCS's HEARTBEAT (sysid 255) must NOT bump our watchdog.
        from tests.test_flight_fixes import FakeConn, Msg
        conn = FakeConn(script=[
            Msg("HEARTBEAT", base_mode=0),          # from the co-GCS
            Msg("PARAM_VALUE", param_id="X", param_value=1.0, param_count=1),
        ])
        # Make the two messages come from different systems.
        conn.script[0].get_srcSystem = lambda: 255
        conn.script[1].get_srcSystem = lambda: 1
        vm = VehicleManager()
        vm.connection = conn
        vm.connected = True
        vm._vehicle_sysid = 1
        stale = time.time() - 100
        vm._last_heartbeat = stale
        vm._recv_blocking(conn, "PARAM_VALUE", 2)
        # The foreign heartbeat was filtered, so the watchdog clock never moved.
        self.assertEqual(vm._last_heartbeat, stale)
        self.assertEqual(vm._msgs_filtered, 1)


class StateMachineTests(unittest.TestCase):
    def test_set_state_logs_transition(self):
        vm = make_vm()
        vm._set_state(LinkState.CONNECTING)
        e = last_event("link", "state")
        self.assertIsNotNone(e)
        self.assertEqual(e["from_state"], LinkState.DISCONNECTED)
        self.assertEqual(e["to_state"], LinkState.CONNECTING)

    def test_same_state_is_noop(self):
        vm = make_vm()
        vm._set_state(LinkState.READY)
        first = vm._state_since
        time.sleep(0.01)
        vm._set_state(LinkState.READY)  # no-op, timestamp unchanged
        self.assertEqual(vm._state_since, first)

    def test_degraded_recovers_to_ready(self):
        vm = make_vm()
        vm._set_state(LinkState.READY)
        vm._set_state(LinkState.DEGRADED)
        vm._set_state(LinkState.READY)
        self.assertEqual(vm._link_state, LinkState.READY)


class LinkLevelTests(unittest.TestCase):
    def test_graded_levels(self):
        vm = make_vm()
        self.assertIsNone(vm._link_level(None))
        self.assertEqual(vm._link_level(0.5), "good")
        self.assertEqual(vm._link_level(2.0), "nominal")
        self.assertEqual(vm._link_level(4.0), "degraded")
        self.assertEqual(vm._link_level(8.0), "poor")
        self.assertEqual(vm._link_level(12.0), "critical")


class CapabilitiesTests(unittest.TestCase):
    def test_autopilot_version_decoded(self):
        vm = make_vm()
        caps = _CAP_BITS["mission_int"] | _CAP_BITS["command_int"] | _CAP_BITS["ftp"]
        # flight_sw_version packs major<<24 | minor<<16 | patch<<8 | type.
        version = (4 << 24) | (5 << 16) | (2 << 8) | 255
        vm._handle_msg(msg("AUTOPILOT_VERSION", capabilities=caps,
                           flight_sw_version=version, vendor_id=0x2DAE, product_id=0x1011))
        c = vm._capabilities
        self.assertTrue(c["mission_int"])
        self.assertTrue(c["command_int"])
        self.assertTrue(c["ftp"])
        self.assertFalse(c["mission_rally"])
        self.assertEqual(c["fw_version"], "4.5.2")
        self.assertEqual(c["vendor_id"], 0x2DAE)

    def test_capabilities_event_logged(self):
        vm = make_vm()
        vm._handle_msg(msg("AUTOPILOT_VERSION", capabilities=_CAP_BITS["mission_int"],
                           flight_sw_version=0))
        e = last_event("link", "capabilities")
        self.assertIsNotNone(e)
        self.assertTrue(e["mission_int"])


class BatteryStatusTests(unittest.TestCase):
    def test_consumed_and_remaining(self):
        vm = make_vm()
        vm._handle_msg(msg("BATTERY_STATUS", current_consumed=1234,
                           battery_remaining=77))
        self.assertEqual(vm.telemetry.battery_consumed_mah, 1234.0)
        self.assertEqual(vm.telemetry.battery_level, 77)

    def test_unknown_values_ignored(self):
        vm = make_vm()
        vm.telemetry.battery_level = 50
        vm._handle_msg(msg("BATTERY_STATUS", current_consumed=-1,
                           battery_remaining=-1))
        self.assertIsNone(vm.telemetry.battery_consumed_mah)
        self.assertEqual(vm.telemetry.battery_level, 50)  # untouched


class SnapshotTests(unittest.TestCase):
    def test_snapshot_link_fields(self):
        vm = make_vm()
        snap = vm.snapshot()
        for key in ("link_state", "link_level", "gcs_sysid", "vehicle_sysid",
                    "capabilities", "msgs_filtered"):
            self.assertIn(key, snap)
        self.assertEqual(snap["link_state"], LinkState.DISCONNECTED)
        self.assertEqual(snap["gcs_sysid"], 252)  # config default != 255
        self.assertIsNone(snap["vehicle_sysid"])

    def test_snapshot_reflects_locked_vehicle(self):
        vm = make_vm()
        vm.connected = True
        vm._connected_at = time.time() - 5
        vm._last_heartbeat = time.time()
        vm._vehicle_sysid = 1
        vm._set_state(LinkState.READY)
        snap = vm.snapshot()
        self.assertEqual(snap["vehicle_sysid"], 1)
        self.assertEqual(snap["link_state"], LinkState.READY)
        self.assertEqual(snap["link_level"], "good")


if __name__ == "__main__":
    unittest.main()

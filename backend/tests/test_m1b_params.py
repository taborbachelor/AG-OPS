"""M1b regression suite: verified parameter writes.

The old path was fire-and-forget with a hardcoded REAL32 type and an
unconditional "ok". M1b reads the param's real type, sends PARAM_SET with it,
waits for the vehicle's PARAM_VALUE echo, and reports the *actual accepted
value* — so SafetyPanel/PARAMS can never claim a fence/failsafe took when it
didn't.

Uses a faithful fake that models an ArduPilot parameter store (typed values,
spontaneous echo on PARAM_SET, silent on unknown params) — no live vehicle.
"""
import time
import unittest

from fastapi import HTTPException
from pymavlink import mavutil

from app.vehicle_manager import VehicleManager
from app.routers import safety as safety_router
from app.routers import vehicle as vehicle_router

REAL32 = mavutil.mavlink.MAV_PARAM_TYPE_REAL32
INT8 = mavutil.mavlink.MAV_PARAM_TYPE_INT8
INT32 = mavutil.mavlink.MAV_PARAM_TYPE_INT32


class PV:
    """Fake PARAM_VALUE message."""

    def __init__(self, name, value, ptype, index=0, count=1):
        self.param_id = name
        self.param_value = float(value)
        self.param_type = ptype
        self.param_index = index
        self.param_count = count

    def get_type(self):
        return "PARAM_VALUE"


class FakeMav:
    """The conn.mav side: records sends and drives the connection's outbox the
    way a real ArduPilot would respond."""

    def __init__(self, conn):
        self.conn = conn
        self.calls = []

    def heartbeat_send(self, *a, **k):
        self.calls.append(("heartbeat_send", a))

    def _decode(self, name):
        return name.decode() if isinstance(name, (bytes, bytearray)) else name

    def param_request_read_send(self, sysid, comp, name, index):
        n = self._decode(name)
        self.calls.append(("param_request_read_send", n or index))
        if n:
            self.conn._emit_current(n)          # by name
        else:
            self.conn._emit_index(index)        # by index (gap-fill)

    def param_request_list_send(self, sysid, comp):
        self.calls.append(("param_request_list_send", None))
        self.conn._emit_list()

    def param_set_send(self, sysid, comp, name, value, ptype):
        n = self._decode(name)
        self.calls.append(("param_set_send", (n, value, ptype)))
        self.conn._apply_set(n, value, ptype)


class FakeParamConn:
    """Models an ArduPilot parameter store.

    store: {name: (value, MAV_PARAM_TYPE)}. Unknown params get no reply (ArduPilot
    ignores them). `transform` models a value the FC clamps/coerces on write.
    `drop_echo_for` drops the spontaneous echo N times (lossy radio) to exercise
    the read-back fallback. `drop_from_list` omits those indices from the bulk
    PARAM_VALUE list stream (still answerable by index, for gap-fill)."""

    target_system = 1
    target_component = 1

    def __init__(self, store, transform=None, drop_echo_for=None, drop_from_list=None):
        self.store = {k: (float(v), t) for k, (v, t) in store.items()}
        self.transform = transform or {}
        self.drop_echo_for = dict(drop_echo_for or {})
        self.drop_from_list = set(drop_from_list or ())
        # Stable index assignment (insertion order), like a real param table.
        self._names = list(self.store.keys())
        self.mav = FakeMav(self)
        self._outbox = []

    def _index_of(self, name):
        return self._names.index(name)

    def _pv(self, name):
        v, t = self.store[name]
        return PV(name, v, t, index=self._index_of(name), count=len(self._names))

    def _emit_current(self, name):
        if name in self.store:
            self._outbox.append(self._pv(name))

    def _emit_index(self, index):
        if 0 <= index < len(self._names):
            self._outbox.append(self._pv(self._names[index]))

    def _emit_list(self):
        for i, name in enumerate(self._names):
            if i not in self.drop_from_list:
                self._outbox.append(self._pv(name))

    def _apply_set(self, name, value, ptype):
        if name not in self.store:
            return  # unknown param: no store, no echo
        _, t = self.store[name]
        stored = self.transform.get(name, value)
        self.store[name] = (float(stored), t)
        drops = self.drop_echo_for.get(name, 0)
        if drops > 0:
            self.drop_echo_for[name] = drops - 1
            return  # echo lost on the wire this time
        self._outbox.append(self._pv(name))

    def recv_match(self, type=None, blocking=False, timeout=None):
        if self._outbox:
            return self._outbox.pop(0)
        if blocking and timeout:
            time.sleep(min(timeout, 0.005))
        return None


def make_vm(conn):
    vm = VehicleManager()
    vm.connection = conn
    vm.connected = True
    vm._PARAM_TIMEOUT = 0.08   # keep the read/echo waits snappy in tests
    return vm


class SingleParamTests(unittest.TestCase):
    def test_verified_write_reports_accepted_value(self):
        conn = FakeParamConn({"FENCE_RADIUS": (300.0, REAL32)})
        vm = make_vm(conn)
        r = vm.set_param("FENCE_RADIUS", 2500.0)
        self.assertTrue(r["verified"])
        self.assertTrue(r["ok"])
        self.assertAlmostEqual(r["accepted"], 2500.0)
        self.assertAlmostEqual(r["previous"], 300.0)

    def test_uses_the_params_real_type_not_hardcoded_real32(self):
        # FENCE_ACTION is an INT8 in the store; the PARAM_SET must carry INT8,
        # proving we read the type instead of blindly sending REAL32.
        conn = FakeParamConn({"FENCE_ACTION": (1, INT8)})
        vm = make_vm(conn)
        r = vm.set_param("FENCE_ACTION", 6)
        self.assertTrue(r["verified"])
        self.assertEqual(r["param_type"], INT8)
        sets = [c for c in conn.mav.calls if c[0] == "param_set_send"]
        self.assertEqual(sets[-1][1][2], INT8)

    def test_int_param_compares_exactly(self):
        conn = FakeParamConn({"FS_GCS_ENABL": (0, INT8)})
        vm = make_vm(conn)
        self.assertTrue(vm.set_param("FS_GCS_ENABL", 1)["verified"])

    def test_clamped_value_is_not_falsely_verified(self):
        # FC stores 200 though we asked for 2500 (an in-range value, so M3
        # validation lets it through) — the mismatch must NOT verify.
        conn = FakeParamConn({"FENCE_RADIUS": (300.0, REAL32)},
                             transform={"FENCE_RADIUS": 200.0})
        vm = make_vm(conn)
        r = vm.set_param("FENCE_RADIUS", 2500.0)
        self.assertFalse(r["verified"])
        self.assertFalse(r["ok"])
        self.assertAlmostEqual(r["accepted"], 200.0)
        self.assertIn("error", r)

    def test_unknown_param_reports_no_echo(self):
        conn = FakeParamConn({})  # empty store: FC ignores everything
        vm = make_vm(conn)
        r = vm.set_param("NOT_A_PARAM", 1.0)
        self.assertFalse(r["verified"])
        self.assertIsNone(r["accepted"])
        self.assertIn("no PARAM_VALUE echo", r["error"])

    def test_dropped_echo_recovered_by_readback(self):
        # First echo is lost; the read-back fallback still confirms the store.
        conn = FakeParamConn({"BATT_LOW_VOLT": (10.5, REAL32)},
                             drop_echo_for={"BATT_LOW_VOLT": 1})
        vm = make_vm(conn)
        r = vm.set_param("BATT_LOW_VOLT", 11.2)
        self.assertTrue(r["verified"])
        self.assertAlmostEqual(r["accepted"], 11.2, places=3)

    def test_not_connected_returns_structured_failure(self):
        vm = VehicleManager()  # no connection
        r = vm.set_param("FENCE_RADIUS", 100.0)
        self.assertFalse(r["verified"])
        self.assertEqual(r["error"], "not connected")


class BatchParamTests(unittest.TestCase):
    def test_all_verified_reports_ok(self):
        conn = FakeParamConn({
            "FENCE_TYPE": (0, INT8), "FENCE_RADIUS": (300.0, REAL32),
            "FENCE_ENABLE": (0, INT8),
        })
        vm = make_vm(conn)
        res = vm.set_params({"FENCE_TYPE": 3, "FENCE_RADIUS": 2500.0,
                             "FENCE_ENABLE": 1})
        self.assertTrue(res["ok"])
        self.assertEqual(res["verified"], 3)
        self.assertEqual(res["failed"], [])

    def test_partial_failure_flags_the_bad_param(self):
        conn = FakeParamConn(
            {"FENCE_RADIUS": (300.0, REAL32), "FENCE_ENABLE": (0, INT8)},
            transform={"FENCE_RADIUS": 200.0})  # radius clamped -> unverified
        vm = make_vm(conn)
        res = vm.set_params({"FENCE_RADIUS": 2500.0, "FENCE_ENABLE": 1})
        self.assertFalse(res["ok"])
        self.assertEqual(res["failed"], ["FENCE_RADIUS"])
        self.assertTrue(res["results"]["FENCE_ENABLE"]["verified"])

    def test_batch_not_connected(self):
        vm = VehicleManager()
        res = vm.set_params({"X": 1})
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "not connected")


class EndpointReportingTests(unittest.TestCase):
    """The routers must surface an unverified write as a 502, not a fake 'ok'."""

    def _install(self, conn):
        vm = make_vm(conn)
        self._orig = safety_router.vehicle_manager
        safety_router.vehicle_manager = vm
        vehicle_router.vehicle_manager = vm
        return vm

    def tearDown(self):
        if hasattr(self, "_orig"):
            safety_router.vehicle_manager = self._orig
            vehicle_router.vehicle_manager = self._orig

    def test_geofence_ok(self):
        conn = FakeParamConn({
            "FENCE_TYPE": (0, INT8), "FENCE_RADIUS": (300.0, REAL32),
            "FENCE_ALT_MAX": (120.0, REAL32), "FENCE_ACTION": (1, INT8),
            "FENCE_ENABLE": (0, INT8),
        })
        self._install(conn)
        body = safety_router.set_geofence(
            safety_router.GeofenceConfig(enable=True, radius=2500, alt_max=150,
                                         action=1))
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["verified"])

    def test_geofence_unverified_raises_502(self):
        conn = FakeParamConn(
            {"FENCE_TYPE": (0, INT8), "FENCE_RADIUS": (300.0, REAL32),
             "FENCE_ALT_MAX": (120.0, REAL32), "FENCE_ACTION": (1, INT8),
             "FENCE_ENABLE": (0, INT8)},
            transform={"FENCE_RADIUS": 200.0})  # FC stores 200, not 2500
        self._install(conn)
        with self.assertRaises(HTTPException) as ctx:
            safety_router.set_geofence(
                safety_router.GeofenceConfig(enable=True, radius=2500,
                                             alt_max=150, action=1))
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn("FENCE_RADIUS", ctx.exception.detail["failed"])

    def test_set_param_endpoint_ok(self):
        conn = FakeParamConn({"WP_RADIUS": (30.0, REAL32)})
        self._install(conn)
        body = vehicle_router.set_param(
            vehicle_router.ParamUpdate(name="WP_RADIUS", value=45.0))
        self.assertEqual(body["status"], "ok")
        self.assertAlmostEqual(body["value"], 45.0)

    def test_set_param_endpoint_unverified_raises_502(self):
        conn = FakeParamConn({})  # unknown param -> no echo
        self._install(conn)
        with self.assertRaises(HTTPException) as ctx:
            vehicle_router.set_param(
                vehicle_router.ParamUpdate(name="BOGUS", value=1.0))
        self.assertEqual(ctx.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()

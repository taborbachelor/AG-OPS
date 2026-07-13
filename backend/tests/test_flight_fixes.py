"""Regression tests for the flight-lane audit fixes.

Covers, with no live vehicle needed (fakes model real mavutil semantics):
  0. lock-held transactions must bump the heartbeat watchdog (no false link loss)
  1. operator disconnect() during an in-flight connect() must win (no silent re-link)
  2. /rc WebSocket survives malformed frames; send_rc_override never raises
  3. blocking endpoints are threadpool `def`, not event-loop `async def`
  4. set_mode is ack-checked; LAND is not advertised (ArduPlane has no LAND mode)
  5. /disarm propagates a rejected/unacked disarm as an error
  6. MAVLink sends are serialized by a send lock (pymavlink packer isn't thread-safe)
  7. connect() failure paths close the port (no leaked half-open COM handle)
  8. POST /connect is rejected while auto-reconnect is in progress
  9. mission upload answers the vehicle's requested seq; aborts cancel the transaction
"""

import asyncio
import inspect
import threading
import time
import unittest
from unittest import mock

from fastapi import HTTPException, WebSocketDisconnect
from pymavlink import mavutil

import app.vehicle_manager as vm_module
from app.vehicle_manager import VehicleManager, vehicle_manager
from app.routers import connection as connection_router
from app.routers import mission as mission_router
from app.routers import safety as safety_router
from app.routers import vehicle as vehicle_router


class Msg:
    """Minimal stand-in for a pymavlink message."""

    def __init__(self, mtype, **kw):
        self._mtype = mtype
        self.__dict__.update(kw)

    def get_type(self):
        return self._mtype


class RecorderMav:
    """Records every *_send call made on conn.mav."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if not name.endswith("_send"):
            raise AttributeError(name)

        def _send(*args, **kwargs):
            self.calls.append((name, args))

        return _send


class FakeConn:
    """Fake mavutil connection: recv_match pops a script of messages.

    A `None` entry models one recv timeout (sleeps briefly like a real
    blocking read would, so deadline-based loops still terminate fast)."""

    target_system = 1
    target_component = 1

    def __init__(self, script=None, mode_map=None):
        self.script = list(script or [])
        self.closed = False
        self.mav = RecorderMav()
        self._mode_map = mode_map or {"MANUAL": 0, "AUTO": 10, "FBWA": 5}

    def recv_match(self, type=None, blocking=False, timeout=None):
        if self.script:
            nxt = self.script.pop(0)
            if nxt is None and blocking and timeout:
                time.sleep(min(timeout, 0.02))
            return nxt
        if blocking and timeout:
            time.sleep(min(timeout, 0.02))
        return None

    def mode_mapping(self):
        return self._mode_map

    def param_fetch_one(self, name):
        self.mav.calls.append(("param_fetch_one", (name,)))

    def param_set_send(self, name, value):
        self.mav.calls.append(("param_set_send", (name, value)))

    def close(self):
        self.closed = True


def make_vm(conn):
    vm = VehicleManager()
    vm.connection = conn
    vm.connected = True
    return vm


# --------------------------------------------------------------------------
# Finding 0: lock-held transactions must not starve the link watchdog
# --------------------------------------------------------------------------
class HeartbeatTrackingTests(unittest.TestCase):
    def test_get_params_bumps_watchdog_on_passing_heartbeats(self):
        conn = FakeConn(script=[
            Msg("HEARTBEAT", base_mode=0),
            Msg("HEARTBEAT", base_mode=0),
            Msg("PARAM_VALUE", param_id="FOO", param_value=42.0, param_count=1),
        ])
        vm = make_vm(conn)
        vm._last_heartbeat = time.time() - 100  # stale: watchdog about to fire
        result = vm.get_params(["FOO"])
        self.assertEqual(result, {"FOO": 42.0})
        self.assertLess(time.time() - vm._last_heartbeat, 5.0,
                        "HEARTBEATs consumed during a lock-held transaction "
                        "must bump _last_heartbeat or the watchdog tears down "
                        "a healthy link right after the lock is released")

    def test_wait_command_ack_bumps_watchdog_and_matches_command(self):
        cmd = mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM
        conn = FakeConn(script=[
            Msg("HEARTBEAT", base_mode=0),
            Msg("COMMAND_ACK", command=999, result=0),
            Msg("COMMAND_ACK", command=cmd,
                result=mavutil.mavlink.MAV_RESULT_ACCEPTED),
        ])
        vm = make_vm(conn)
        vm._last_heartbeat = time.time() - 100
        ack = vm._wait_command_ack(cmd, timeout=2)
        self.assertIsNotNone(ack)
        self.assertEqual(ack.command, cmd)
        self.assertLess(time.time() - vm._last_heartbeat, 5.0)

    def test_recv_blocking_sends_gcs_heartbeat_during_transaction(self):
        conn = FakeConn(script=[None, Msg("PARAM_VALUE", param_id="X",
                                          param_value=1.0, param_count=1)])
        vm = make_vm(conn)
        vm._last_hb_sent = 0.0  # heartbeat overdue
        vm._recv_blocking(conn, "PARAM_VALUE", 2)
        sent = [c for c in conn.mav.calls if c[0] == "heartbeat_send"]
        self.assertTrue(sent, "long transactions must keep sending the 1Hz GCS "
                              "heartbeat or the vehicle's FS_GCS failsafe fires")


# --------------------------------------------------------------------------
# Finding 1: operator disconnect during an in-flight connect must win
# --------------------------------------------------------------------------
class DisconnectDuringConnectTests(unittest.TestCase):
    def test_disconnect_mid_connect_aborts_and_never_rearms(self):
        vm = VehicleManager()

        class HalfConn(FakeConn):
            def wait_heartbeat(self, timeout=None):
                # Operator hits Disconnect while we're still dialing/waiting.
                vm.disconnect()
                return Msg("HEARTBEAT", base_mode=0)

        conn = HalfConn()
        with mock.patch.object(vm_module.mavutil, "mavlink_connection",
                               return_value=conn):
            with self.assertRaises(ConnectionError):
                vm.connect("tcp:127.0.0.1:5760")
        self.assertFalse(vm.connected)
        self.assertIsNone(vm.connection)
        self.assertFalse(vm._auto_reconnect,
                         "connect() must never re-arm auto-reconnect over an "
                         "operator's explicit disconnect()")
        self.assertTrue(conn.closed)


# --------------------------------------------------------------------------
# Finding 2: /rc WebSocket survives malformed frames
# --------------------------------------------------------------------------
class FakeWS:
    def __init__(self, frames):
        self.frames = list(frames)

    async def accept(self):
        pass

    async def receive_json(self):
        if not self.frames:
            raise WebSocketDisconnect(1000)
        return self.frames.pop(0)


class RcWebSocketTests(unittest.TestCase):
    def test_malformed_frame_does_not_kill_handler(self):
        received = []
        released = []
        frames = [
            {"channels": [1900] * 8},
            {"channels": {"oops": 1}},   # valid JSON, wrong shape
            "junk",                       # not even a dict
            {"channels": [1700] * 8},
            {"nochannels": True},
            {"channels": [1300] * 8},
        ]
        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "send_rc_override",
                               side_effect=lambda ch: received.append(ch)), \
             mock.patch.object(vehicle_manager, "release_rc_override",
                               side_effect=lambda: released.append(True)):
            asyncio.run(vehicle_router.rc_override_ws(FakeWS(frames)))
        self.assertEqual(received,
                         [[1900] * 8, [1700] * 8, [1300] * 8],
                         "frames after a malformed one must still be forwarded")
        self.assertTrue(released, "override must be released on disconnect")

    def test_handler_survives_send_raising(self):
        calls = []

        def flaky(ch):
            calls.append(ch)
            if len(calls) == 1:
                raise RuntimeError("link died mid-send")

        frames = [{"channels": [1500] * 8}, {"channels": [1600] * 8}]
        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "send_rc_override",
                               side_effect=flaky), \
             mock.patch.object(vehicle_manager, "release_rc_override"):
            asyncio.run(vehicle_router.rc_override_ws(FakeWS(frames)))
        self.assertEqual(len(calls), 2,
                         "an exception in one send must not end the stream")

    def test_send_rc_override_rejects_malformed_without_raising(self):
        vm = make_vm(FakeConn())
        self.assertFalse(vm.send_rc_override({"oops": 1}))
        self.assertFalse(vm.send_rc_override("junk"))
        self.assertTrue(vm.send_rc_override([1500] * 8))

    def test_send_rc_override_handles_connection_nulled_mid_call(self):
        vm = make_vm(FakeConn())
        vm.connection = None  # link loss raced us
        self.assertFalse(vm.send_rc_override([1500] * 8))


# --------------------------------------------------------------------------
# Finding 3: blocking endpoints must run in the threadpool, not the event loop
# --------------------------------------------------------------------------
class ThreadpoolHandlerTests(unittest.TestCase):
    BLOCKING_HANDLERS = [
        connection_router.connect_vehicle,
        connection_router.disconnect_vehicle,
        connection_router.list_serial_ports,
        vehicle_router.set_mode,
        vehicle_router.arm,
        vehicle_router.disarm,
        vehicle_router.takeoff,
        vehicle_router.land,
        vehicle_router.get_all_params,
        vehicle_router.set_param,
        mission_router.download_mission,
        mission_router.upload_mission,
        mission_router.start_mission,
        safety_router.get_geofence,
        safety_router.set_geofence,
        safety_router.get_failsafe,
        safety_router.set_failsafe,
    ]

    def test_blocking_handlers_are_not_coroutines(self):
        for fn in self.BLOCKING_HANDLERS:
            self.assertFalse(
                inspect.iscoroutinefunction(fn),
                f"{fn.__module__}.{fn.__name__} is async def but calls blocking "
                f"pymavlink code — it would freeze the whole GCS API "
                f"(including emergency RTL/disarm) while it runs")


# --------------------------------------------------------------------------
# Finding 4: set_mode ack-checked; LAND not advertised
# --------------------------------------------------------------------------
class SetModeTests(unittest.TestCase):
    def test_land_not_in_available_modes(self):
        self.assertNotIn("LAND", vehicle_manager.get_available_modes(),
                         "ArduPlane has no LAND mode; advertising it lets the "
                         "operator command a landing that never happens")

    def test_unknown_mode_returns_false_and_sends_nothing(self):
        conn = FakeConn(mode_map={"MANUAL": 0, "AUTO": 10})
        vm = make_vm(conn)
        self.assertFalse(vm.set_mode("LAND"))
        self.assertEqual(conn.mav.calls, [])

    def test_accepted_ack_returns_true(self):
        conn = FakeConn(script=[
            Msg("COMMAND_ACK", command=mavutil.mavlink.MAV_CMD_DO_SET_MODE,
                result=mavutil.mavlink.MAV_RESULT_ACCEPTED)])
        vm = make_vm(conn)
        self.assertTrue(vm.set_mode("FBWA"))

    def test_denied_ack_returns_false(self):
        conn = FakeConn(script=[
            Msg("COMMAND_ACK", command=mavutil.mavlink.MAV_CMD_DO_SET_MODE,
                result=mavutil.mavlink.MAV_RESULT_DENIED)])
        vm = make_vm(conn)
        self.assertFalse(vm.set_mode("FBWA"))

    def test_mode_router_propagates_rejection(self):
        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "set_mode", return_value=False):
            with self.assertRaises(HTTPException) as ctx:
                vehicle_router.set_mode(vehicle_router.ModeRequest(mode="LAND"))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_mission_start_propagates_rejection(self):
        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "set_mode", return_value=False):
            with self.assertRaises(HTTPException) as ctx:
                mission_router.start_mission()
        self.assertEqual(ctx.exception.status_code, 400)


# --------------------------------------------------------------------------
# Finding 5: /disarm must not report success on rejection / missing ack
# --------------------------------------------------------------------------
class DisarmRouterTests(unittest.TestCase):
    def test_rejected_disarm_returns_400(self):
        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "disarm",
                               return_value={"ok": False,
                                             "error": "vehicle rejected disarm"}):
            with self.assertRaises(HTTPException) as ctx:
                vehicle_router.disarm(vehicle_router.ArmRequest())
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("rejected", ctx.exception.detail)

    def test_acked_disarm_returns_disarmed(self):
        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "disarm",
                               return_value={"ok": True, "result": 0,
                                             "error": None}):
            out = vehicle_router.disarm(vehicle_router.ArmRequest())
        self.assertEqual(out["status"], "disarmed")

    def test_manager_disarm_reports_no_ack(self):
        vm = make_vm(FakeConn())  # script empty -> no COMMAND_ACK ever
        # keep it fast: shrink the ack wait
        with mock.patch.object(vm, "_wait_command_ack", return_value=None):
            result = vm.disarm()
        self.assertFalse(result["ok"])
        self.assertIn("acknowledgement", result["error"])


# --------------------------------------------------------------------------
# Finding 6: concurrent sends must be serialized by the send lock
# --------------------------------------------------------------------------
class SendLockTests(unittest.TestCase):
    def test_concurrent_rc_and_heartbeat_sends_never_overlap(self):
        class Probe:
            target_system = 1
            target_component = 1

            def __init__(self):
                self.busy = False
                self.violations = 0
                self.count = 0
                self.mav = self

            def _enter(self):
                if self.busy:
                    self.violations += 1
                self.busy = True
                time.sleep(0.0005)  # models the serial write window
                self.busy = False
                self.count += 1

            def rc_channels_override_send(self, *a):
                self._enter()

            def heartbeat_send(self, *a):
                self._enter()

        probe = Probe()
        vm = make_vm(probe)
        start = threading.Barrier(5)

        def rc_worker():
            start.wait()
            for _ in range(50):
                vm.send_rc_override([1500] * 8)

        def hb_worker():
            start.wait()
            for _ in range(50):
                vm._last_hb_sent = 0.0  # force the rate limiter open
                vm._send_gcs_heartbeat(probe)

        threads = [threading.Thread(target=rc_worker) for _ in range(3)]
        threads.append(threading.Thread(target=hb_worker))
        for t in threads:
            t.start()
        start.wait()
        for t in threads:
            t.join()
        self.assertEqual(probe.count, 200)
        self.assertEqual(probe.violations, 0,
                         "pymavlink's packer is not thread-safe: all sends "
                         "must go through _send_lock")


# --------------------------------------------------------------------------
# Finding 7: failed connects must close the port (and never false-succeed)
# --------------------------------------------------------------------------
class ConnectCleanupTests(unittest.TestCase):
    def test_exception_during_wait_heartbeat_closes_port(self):
        class DeadConn(FakeConn):
            def wait_heartbeat(self, timeout=None):
                raise OSError("serial gone")

        conn = DeadConn()
        vm = VehicleManager()
        with mock.patch.object(vm_module.mavutil, "mavlink_connection",
                               return_value=conn):
            with self.assertRaises(ConnectionError):
                vm.connect("COM7")
        self.assertTrue(conn.closed,
                        "a leaked open COM handle blocks every future "
                        "(re)connect until the backend restarts")
        self.assertIsNone(vm.connection)
        self.assertFalse(vm.connected)

    def test_silent_link_is_a_failure_not_a_false_success(self):
        class SilentConn(FakeConn):
            def wait_heartbeat(self, timeout=None):
                return None  # mavutil returns None on timeout — never raises

        conn = SilentConn()
        vm = VehicleManager()
        with mock.patch.object(vm_module.mavutil, "mavlink_connection",
                               return_value=conn):
            with self.assertRaises(ConnectionError):
                vm.connect("COM7")
        self.assertTrue(conn.closed)
        self.assertFalse(vm.connected,
                         "no heartbeat means no vehicle: connect() must not "
                         "report success on a silent link")


# --------------------------------------------------------------------------
# Finding 8: manual connect must be rejected while auto-reconnect runs
# --------------------------------------------------------------------------
class ConnectGuardTests(unittest.TestCase):
    def test_connect_rejected_while_reconnecting(self):
        with mock.patch.object(vehicle_manager, "connected", False), \
             mock.patch.object(vehicle_manager, "reconnecting", True):
            with self.assertRaises(HTTPException) as ctx:
                connection_router.connect_vehicle(
                    connection_router.ConnectRequest(
                        connection_string="tcp:127.0.0.1:5760"))
        self.assertEqual(ctx.exception.status_code, 409)


# --------------------------------------------------------------------------
# Finding 9: mission upload must answer the REQUESTED seq and cancel on abort
# --------------------------------------------------------------------------
class MissionVehicle:
    """Emulates ArduPilot's upload state machine: wants items in order,
    re-requests the current seq when an item is lost or has the wrong seq."""

    target_system = 1
    target_component = 1

    def __init__(self, drop_first_item=False):
        self.pending = []
        self.stored = {}
        self.count = None
        self.want = 0
        self.drop_first_item = drop_first_item
        self._dropped = False
        self.requests = []
        self.gcs_acks = []
        self.mav = self

    def _request(self, seq):
        self.requests.append(seq)
        self.pending.append(Msg("MISSION_REQUEST", seq=seq))

    # --- GCS -> vehicle ---
    def mission_count_send(self, sysid, compid, count):
        self.count = count
        self.want = 0
        self._request(0)

    def mission_item_int_send(self, sysid, compid, seq, frame, cmd, cur, auto,
                              p1, p2, p3, p4, x, y, z):
        if self.drop_first_item and not self._dropped:
            self._dropped = True
            self._request(self.want)  # item lost over RF -> re-request same seq
            return
        if seq != self.want:
            self._request(self.want)  # wrong item -> re-request what we want
            return
        self.stored[seq] = {"cmd": cmd, "x": x, "y": y, "z": z}
        self.want += 1
        if self.want >= self.count:
            self.pending.append(
                Msg("MISSION_ACK", type=mavutil.mavlink.MAV_MISSION_ACCEPTED))
        else:
            self._request(self.want)

    def mission_ack_send(self, sysid, compid, ack_type):
        self.gcs_acks.append(ack_type)

    def heartbeat_send(self, *a):
        pass

    # --- vehicle -> GCS ---
    def recv_match(self, type=None, blocking=False, timeout=None):
        if self.pending:
            return self.pending.pop(0)
        if blocking and timeout:
            time.sleep(min(timeout, 0.02))
        return None


class MissionUploadTests(unittest.TestCase):
    ITEMS = [
        {"command": "TAKEOFF", "lat": 39.0, "lon": -95.0, "alt": 50.0},
        {"command": "WAYPOINT", "lat": 39.001, "lon": -95.001, "alt": 60.0},
    ]

    def test_retransmitted_request_does_not_desync_transfer(self):
        veh = MissionVehicle(drop_first_item=True)
        vm = make_vm(veh)
        vm.telemetry.lat, vm.telemetry.lon = 39.0, -95.0
        result = vm.upload_mission(list(self.ITEMS))
        self.assertTrue(result["ok"], result)
        # home + 2 items, all stored under the seq the vehicle asked for
        self.assertEqual(sorted(veh.stored), [0, 1, 2])
        self.assertEqual(veh.requests.count(0), 2,
                         "scenario must include the lossy-radio re-request")
        self.assertEqual(veh.stored[1]["x"], int(39.0 * 1e7))
        self.assertEqual(veh.stored[2]["x"], int(39.001 * 1e7))
        self.assertEqual(vm.telemetry.mission_count, 2)

    def test_timed_out_upload_cancels_transaction(self):
        class MuteVehicle(MissionVehicle):
            def recv_match(self, type=None, blocking=False, timeout=None):
                if blocking and timeout:
                    time.sleep(min(timeout, 0.05))
                return None  # vehicle never answers

        veh = MuteVehicle()
        vm = make_vm(veh)
        vm.telemetry.lat, vm.telemetry.lon = 39.0, -95.0
        vm.telemetry.mission_count = 99
        result = vm.upload_mission(list(self.ITEMS))
        self.assertFalse(result["ok"])
        self.assertIn("error", result)
        self.assertEqual(
            veh.gcs_acks,
            [mavutil.mavlink.MAV_MISSION_OPERATION_CANCELLED],
            "an aborted upload must close the transaction so the vehicle "
            "isn't left mid-transfer with a partially replaced mission")
        self.assertEqual(vm.telemetry.mission_count, 99,
                         "mission_count only updates on success")


if __name__ == "__main__":
    unittest.main()

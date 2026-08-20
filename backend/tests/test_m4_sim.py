"""M4 unit tests: sim-router fault injection + GCS heartbeat suppression.

Pure-fake coverage of the new surface so `pytest` (no SITL) still proves the
plumbing; the flight-truth of each fault lives in tests/sitl/ scenarios.
"""
import inspect
import os
import socket
import threading
import time
import unittest
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from app import config, param_meta
from app.main import app
from app.routers import sim
from app.vehicle_manager import VehicleManager, vehicle_manager


class _RecMav:
    def __init__(self):
        self.heartbeats = 0

    def heartbeat_send(self, *a, **k):
        self.heartbeats += 1


class _Conn:
    def __init__(self):
        self.mav = _RecMav()


class TestHeartbeatSuppression(unittest.TestCase):
    def test_suppression_stops_and_restores_gcs_heartbeat(self):
        vm = VehicleManager()
        conn = _Conn()
        vm._last_hb_sent = 0
        vm._send_gcs_heartbeat(conn)
        self.assertEqual(conn.mav.heartbeats, 1)

        vm.set_gcs_heartbeat_suppressed(True)
        vm._last_hb_sent = 0
        vm._send_gcs_heartbeat(conn)
        self.assertEqual(conn.mav.heartbeats, 1, "suppressed heartbeat was sent")
        self.assertTrue(vm.snapshot()["gcs_hb_suppressed"])

        vm.set_gcs_heartbeat_suppressed(False)
        vm._last_hb_sent = 0
        vm._send_gcs_heartbeat(conn)
        self.assertEqual(conn.mav.heartbeats, 2)
        self.assertFalse(vm.snapshot()["gcs_hb_suppressed"])


class TestSimSpawnConfig(unittest.TestCase):
    def test_build_args_carries_speedup(self):
        args = sim._build_args(5.0)
        i = args.index("--speedup")
        self.assertEqual(args[i + 1], "5.0")
        self.assertIn("-M", args)

    def test_resolve_cwd_default_is_binary_dir(self):
        import pathlib
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            exe = pathlib.Path(td) / "ArduPlane.exe"
            exe.write_bytes(b"")
            self.assertEqual(sim._resolve_cwd(exe, fresh_eeprom=False), exe.parent)

    def test_resolve_cwd_fresh_eeprom_isolated_and_wiped(self):
        import pathlib
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            exe = pathlib.Path(td) / "ArduPlane.exe"
            exe.write_bytes(b"")
            demo_eeprom = exe.parent / "eeprom.bin"
            demo_eeprom.write_bytes(b"demo state")
            scratch = exe.parent / sim.SCENARIO_DIR_NAME
            scratch.mkdir()
            (scratch / "eeprom.bin").write_bytes(b"stale scenario state")

            cwd = sim._resolve_cwd(exe, fresh_eeprom=True)
            self.assertEqual(cwd, scratch)
            self.assertFalse((scratch / "eeprom.bin").exists(),
                             "stale scenario eeprom must be wiped")
            self.assertEqual(demo_eeprom.read_bytes(), b"demo state",
                             "demo eeprom next to the binary must never be touched")


class TestSitlInstanceConfig(unittest.TestCase):
    """SITL_INSTANCE is what lets two sessions run the scenario suite at once.

    The suite is single-occupancy on one port; without this, parallel runs fail a
    different set of scenarios each time, always at the connection level.
    """

    def _instance(self, raw):
        env = {k: v for k, v in os.environ.items() if k != "SITL_INSTANCE"}
        if raw is not None:
            env["SITL_INSTANCE"] = raw
        with mock.patch.dict(os.environ, env, clear=True):
            return config._sitl_instance()

    def test_unset_or_blank_is_instance_zero(self):
        self.assertEqual(self._instance(None), 0)
        self.assertEqual(self._instance(""), 0)
        self.assertEqual(self._instance("   "), 0)

    def test_valid_instance_is_read(self):
        self.assertEqual(self._instance("3"), 3)
        self.assertEqual(self._instance(" 2 "), 2)

    def test_garbage_fails_loudly_rather_than_defaulting_to_zero(self):
        """A typo must not silently mean instance 0 — that is the collision this
        setting exists to prevent, and it would look like a working run."""
        for bad in ("one", "1.5", "0x2", "--1"):
            with self.assertRaises(ValueError, msg=f"{bad!r} was accepted"):
                self._instance(bad)

    def test_out_of_range_fails_loudly(self):
        for bad in ("-1", str(config.SITL_MAX_INSTANCE + 1)):
            with self.assertRaises(ValueError, msg=f"{bad!r} was accepted"):
                self._instance(bad)

    def test_port_derives_from_instance_and_is_reported(self):
        self.assertEqual(sim.SITL_PORT,
                         sim.SITL_BASE_PORT + 10 * config.SITL_INSTANCE)
        self.assertEqual(sim.SITL_CONN, f"tcp:127.0.0.1:{sim.SITL_PORT}")

    def test_default_instance_keeps_the_historical_port(self):
        self.assertEqual(sim.SITL_BASE_PORT + 10 * 0, 5760)

    def test_build_args_passes_the_instance_to_sitl(self):
        """-I is what actually offsets the ports; without it every instance
        would still bind 5760 while we merrily reported otherwise."""
        args = sim._build_args(1.0)
        i = args.index("-I")
        self.assertEqual(args[i + 1], str(config.SITL_INSTANCE))

    def test_scenario_scratch_dir_is_per_instance(self):
        """Each SITL writes eeprom.bin into its cwd, so concurrent instances
        sharing one scratch dir would hand each other their parameters."""
        self.assertEqual(sim._scenario_dir_name(0), "_scenario")
        self.assertNotEqual(sim._scenario_dir_name(1), sim._scenario_dir_name(0))
        self.assertNotEqual(sim._scenario_dir_name(2), sim._scenario_dir_name(1))

    def test_status_reports_where_sitl_listens(self):
        """The scenario harness reads the port from here instead of keeping its
        own copy — so status must always carry it, running or not."""
        client = TestClient(app)
        with mock.patch.object(sim, "_running", return_value=False):
            body = client.get("/api/sim/status").json()
        self.assertEqual(body["port"], sim.SITL_PORT)
        self.assertEqual(body["connection_string"], sim.SITL_CONN)
        self.assertEqual(body["instance"], config.SITL_INSTANCE)


class TestWaitPortFree(unittest.TestCase):
    """Teardown's proof that the port really is free. The old code slept a flat
    second and never checked, which is how a scenario ended up connecting to a
    SITL that had not finished dying."""

    def test_returns_immediately_when_free(self):
        with mock.patch.object(sim, "_port_listening", return_value=False) as p:
            self.assertTrue(sim.wait_port_free(timeout=5.0))
        self.assertEqual(p.call_count, 1, "should not sleep when already free")

    def test_returns_false_when_never_frees(self):
        with mock.patch.object(sim, "_port_listening", return_value=True):
            self.assertFalse(sim.wait_port_free(timeout=0.3, interval=0.05))

    def test_waits_for_a_late_release(self):
        states = [True, True, False]
        with mock.patch.object(sim, "_port_listening",
                               side_effect=lambda _port=None: states.pop(0)):
            self.assertTrue(sim.wait_port_free(timeout=5.0, interval=0.01))
        self.assertEqual(states, [])

    def test_never_connect_probes(self):
        """A connect-and-close readiness probe kills this SITL build. The waiter
        must go through the passive check and nothing else."""
        with mock.patch.object(sim, "_port_listening", return_value=False), \
             mock.patch("socket.socket") as sock:
            sim.wait_port_free(timeout=1.0)
        sock.assert_not_called()


class TestStartEndpointCompat(unittest.TestCase):
    def test_start_with_no_body_still_works(self):
        # The UI's Simulator button has always POSTed /api/sim/start with no
        # body — the M4 options must stay strictly additive.
        client = TestClient(app)
        with mock.patch.object(sim, "_start_blocking",
                               return_value={"status": "started"}) as sb:
            r = client.post("/api/sim/start")
        self.assertEqual(r.status_code, 200)
        req = sb.call_args.args[0]
        self.assertEqual((req.speedup, req.fresh_eeprom), (1.0, False))


class TestFaultEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        sim._reset_faults()

    def tearDown(self):
        sim._reset_faults()

    def test_fault_requires_vehicle(self):
        with mock.patch.object(vehicle_manager, "connected", False):
            r = self.client.post("/api/sim/fault", json={"fault": "gps"})
        self.assertEqual(r.status_code, 400)

    def test_gps_fault_uses_new_param_name_when_present(self):
        writes = []

        def fake_set(name, value):
            writes.append((name, value))
            return {"verified": True, "accepted": value, "requested": value}

        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "get_cached_params",
                               return_value={"SIM_SPEEDUP": 1.0}), \
             mock.patch.object(vehicle_manager, "cached_type",
                               side_effect=lambda n: 2 if n == "SIM_GPS1_ENABLE" else None), \
             mock.patch.object(vehicle_manager, "set_param", side_effect=fake_set):
            r = self.client.post("/api/sim/fault",
                                 json={"fault": "gps", "enable": True})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(writes, [("SIM_GPS1_ENABLE", 0.0)])

    def test_gps_fault_falls_back_to_legacy_param(self):
        writes = []

        def fake_set(name, value):
            writes.append((name, value))
            return {"verified": True, "accepted": value, "requested": value}

        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "get_cached_params",
                               return_value={"SIM_SPEEDUP": 1.0}), \
             mock.patch.object(vehicle_manager, "cached_type",
                               side_effect=lambda n: 4 if n == "SIM_GPS_DISABLE" else None), \
             mock.patch.object(vehicle_manager, "set_param", side_effect=fake_set):
            r = self.client.post("/api/sim/fault",
                                 json={"fault": "gps", "enable": False})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(writes, [("SIM_GPS_DISABLE", 0.0)])  # healthy value

    def test_battery_fault_restores_previous_voltage(self):
        writes = []

        def fake_set(name, value):
            writes.append((name, value))
            return {"verified": True, "accepted": value, "requested": value}

        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "get_cached_params",
                               return_value={"SIM_SPEEDUP": 1.0}), \
             mock.patch.object(vehicle_manager, "cached_value",
                               side_effect=lambda n: 12.4 if n == "SIM_BATT_VOLTAGE" else None), \
             mock.patch.object(vehicle_manager, "set_param", side_effect=fake_set):
            r1 = self.client.post("/api/sim/fault",
                                  json={"fault": "battery", "enable": True,
                                        "value": 10.2})
            r2 = self.client.post("/api/sim/fault",
                                  json={"fault": "battery", "enable": False})
        self.assertEqual((r1.status_code, r2.status_code), (200, 200))
        self.assertEqual(writes, [("SIM_BATT_VOLTAGE", 10.2),
                                  ("SIM_BATT_VOLTAGE", 12.4)])

    def test_unverified_fault_write_fails_loudly(self):
        def fake_set(name, value):
            return {"verified": False, "accepted": None, "requested": value,
                    "error": "no PARAM_VALUE echo from vehicle"}

        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "get_cached_params",
                               return_value={"SIM_SPEEDUP": 1.0}), \
             mock.patch.object(vehicle_manager, "cached_type", return_value=2), \
             mock.patch.object(vehicle_manager, "set_param", side_effect=fake_set):
            r = self.client.post("/api/sim/fault", json={"fault": "gps"})
        self.assertEqual(r.status_code, 502)

    def test_fault_refused_on_non_sitl_vehicle(self):
        """gcs_link on a REAL vehicle would silence our heartbeats and
        trigger an actual FS_GCS RTL - a synced cache with no SIM_* params
        identifies real firmware and must refuse every fault."""
        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "get_cached_params",
                               return_value={"FENCE_ENABLE": 1.0}):
            r = self.client.post("/api/sim/fault",
                                 json={"fault": "gcs_link", "enable": True})
        self.assertEqual(r.status_code, 409)
        self.assertFalse(vehicle_manager.snapshot()["gcs_hb_suppressed"])

    def test_fault_refused_when_sitl_unconfirmable(self):
        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "get_cached_params",
                               return_value={}), \
             mock.patch.object(vehicle_manager, "get_param", return_value=None):
            r = self.client.post("/api/sim/fault",
                                 json={"fault": "gcs_link", "enable": True})
        self.assertEqual(r.status_code, 409)

    def test_gcs_link_fault_toggles_suppression(self):
        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "get_cached_params",
                               return_value={"SIM_SPEEDUP": 1.0}):
            r = self.client.post("/api/sim/fault",
                                 json={"fault": "gcs_link", "enable": True})
            self.assertEqual(r.status_code, 200)
            self.assertTrue(vehicle_manager.snapshot()["gcs_hb_suppressed"])
            status = self.client.get("/api/sim/status").json()
            self.assertTrue(status["faults"]["gcs_link"])
            r = self.client.post("/api/sim/fault",
                                 json={"fault": "gcs_link", "enable": False})
            self.assertEqual(r.status_code, 200)
            self.assertFalse(vehicle_manager.snapshot()["gcs_hb_suppressed"])


class TestGuardianProofFaults(unittest.TestCase):
    """The three faults added so the merged guardian monitors could be proven
    against a live telemetry stream (SPRAY-FLIGHT-SAFETY.md Part 3C)."""

    def setUp(self):
        self.client = TestClient(app)
        sim._reset_faults()

    def tearDown(self):
        sim._reset_faults()

    def _fault(self, writes, cached=None, **body):
        def fake_set(name, value):
            writes.append((name, value))
            return {"verified": True, "accepted": value, "requested": value}

        with mock.patch.object(vehicle_manager, "connected", True), \
             mock.patch.object(vehicle_manager, "get_cached_params",
                               return_value={"SIM_SPEEDUP": 1.0}), \
             mock.patch.object(vehicle_manager, "cached_value",
                               side_effect=lambda n: (cached or {}).get(n)), \
             mock.patch.object(vehicle_manager, "set_param", side_effect=fake_set):
            return self.client.post("/api/sim/fault", json=body)

    def test_gps_noise_sets_and_restores_horizontal_noise(self):
        writes = []
        r1 = self._fault(writes, cached={"SIM_GPS1_HNSE": 0.0},
                         fault="gps_noise", enable=True, value=10.0)
        r2 = self._fault(writes, fault="gps_noise", enable=False)
        self.assertEqual((r1.status_code, r2.status_code), (200, 200))
        self.assertEqual(writes, [("SIM_GPS1_HNSE", 10.0),
                                  ("SIM_GPS1_HNSE", 0.0)])

    def test_airspeed_fault_clears_to_zero(self):
        """0 IS the healthy value for SIM_ARSPD_FAIL, so clearing writes 0
        rather than restoring a remembered number."""
        writes = []
        r1 = self._fault(writes, fault="airspeed", enable=True, value=4.0)
        r2 = self._fault(writes, fault="airspeed", enable=False)
        self.assertEqual((r1.status_code, r2.status_code), (200, 200))
        self.assertEqual(writes, [("SIM_ARSPD_FAIL", 4.0),
                                  ("SIM_ARSPD_FAIL", 0.0)])

    def test_there_is_no_vibration_fault(self):
        """Pins a deliberate ABSENCE. SIM_VIB_MOT_* / SIM_ACC1_RND et al. were
        measured to have no effect on a plane SITL (see the note in sim.py), so
        no vibration fault is offered rather than one that lies about
        injecting. If a future build can drive it, add the fault AND a
        scenario — do not just re-add the endpoint."""
        writes = []
        r = self._fault(writes, fault="vibration", enable=True)
        self.assertEqual(r.status_code, 422)   # not in the Literal
        self.assertEqual(writes, [])
        self.assertNotIn("vibration", sim._faults)

    def test_reinjecting_does_not_overwrite_the_remembered_value(self):
        """Re-injecting an active fault must not capture the FAULTED value as
        the restore point — that would make 'clear' restore the fault."""
        writes = []
        self._fault(writes, cached={"SIM_GPS1_HNSE": 0.0},
                    fault="gps_noise", enable=True, value=2.0)
        # Second injection: cached_value now reports the faulted 2.0.
        self._fault(writes, cached={"SIM_GPS1_HNSE": 2.0},
                    fault="gps_noise", enable=True, value=10.0)
        self._fault(writes, cached={"SIM_GPS1_HNSE": 10.0},
                    fault="gps_noise", enable=False)
        self.assertEqual(writes[-1], ("SIM_GPS1_HNSE", 0.0))

    def test_out_of_range_value_is_rejected_per_fault(self):
        writes = []
        r = self._fault(writes, fault="airspeed", enable=True, value=500.0)
        self.assertEqual(r.status_code, 422)
        self.assertEqual(writes, [], "a rejected fault must write nothing")

    def test_new_faults_appear_in_status(self):
        for name in ("gps_noise", "airspeed"):
            self.assertIn(name, sim._faults)


class TestPortDetectionAgainstARealSocket(unittest.TestCase):
    """The one test that proves the port guard actually works.

    Everything else here mocks _port_listening, which proves the WAITING logic
    but assumes away the thing it rests on: that the check really does see a
    listening socket on this platform. If netstat parsing were wrong,
    wait_port_free() would cheerfully report "free" forever and every scenario
    would sail past the guard into the exact collision it exists to stop — and
    the mocked tests would all still pass.

    So: bind a real socket, watch the real check find it, release it, watch the
    check clear. No SITL, no mocks in the detection path.

    Deliberately on an OS-assigned ephemeral port, never SITL_PORT: binding 5760
    here would itself block a SITL another session is legitimately running, and
    a test that sabotages a teammate's run to prove a point about collisions
    would be its own punchline.
    """

    def setUp(self):
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.assertNotEqual(self.port, sim.SITL_PORT,
                            "test must never bind the real SITL port")

    def tearDown(self):
        try:
            self.srv.close()
        except OSError:
            pass

    def test_a_real_listening_socket_is_detected(self):
        self.assertTrue(sim._port_listening(self.port),
                        "a bound, listening socket was not seen — the port "
                        "check is broken and the guard protects nothing")

    def test_a_free_port_reads_as_free(self):
        self.srv.close()
        self.assertTrue(sim.wait_port_free(timeout=20.0, port=self.port))

    def test_wait_does_not_return_while_the_port_is_held(self):
        """The real assertion: it WAITS. Returning False early would be just as
        wrong as returning True — the caller would give up on a port that was
        about to free."""
        timeout = 1.0
        started = time.monotonic()
        result = sim.wait_port_free(timeout=timeout, interval=0.05,
                                    port=self.port)
        elapsed = time.monotonic() - started
        self.assertFalse(result, "reported a held port as free")
        self.assertGreaterEqual(
            elapsed, timeout,
            f"gave up after {elapsed:.2f}s on a {timeout:.1f}s timeout — it "
            f"did not actually wait")

    def test_wait_returns_once_the_port_is_released(self):
        """Held, then released mid-wait: the whole point of the function, since
        SITL frees its port asynchronously after the GCS disconnects."""
        released_at = []

        def release_soon():
            time.sleep(0.6)
            released_at.append(time.monotonic())
            self.srv.close()

        t = threading.Thread(target=release_soon)
        started = time.monotonic()
        t.start()
        try:
            result = sim.wait_port_free(timeout=25.0, interval=0.1,
                                        port=self.port)
        finally:
            t.join()
        elapsed = time.monotonic() - started

        self.assertTrue(result, "did not notice the port being released")
        self.assertTrue(released_at, "release thread never ran")
        self.assertGreaterEqual(
            elapsed, 0.5,
            "returned before the socket was released — it cannot have checked")

    def test_conftest_hard_fails_while_the_port_is_listening(self):
        """The setup guard, end to end against a real socket: real bind -> real
        netstat -> real port_is_free -> real pytest.fail. SITL_PORT is pointed at
        the test's own socket so the whole default path runs untouched.

        This is the behaviour change that matters. Before it, a scenario finding
        the port busy connected anyway and died later with a bare WinError 10061
        that read like a regression in the code under test.
        """
        from tests.sitl import conftest as sitl_conftest

        # Short timeout only so the test does not sit out the real 20s budget;
        # everything in the detection path stays real.
        with mock.patch.object(sim, "SITL_PORT", self.port), \
             mock.patch.object(sitl_conftest, "PORT_FREE_TIMEOUT", 1.0):
            with self.assertRaises(pytest.fail.Exception) as ctx:
                sitl_conftest._require_free_port()

        msg = str(ctx.exception)
        self.assertIn(str(self.port), msg)
        self.assertIn("SITL_INSTANCE", msg)

    def test_conftest_guard_passes_once_the_port_is_free(self):
        """The other half — the guard must not block a legitimate run."""
        from tests.sitl import conftest as sitl_conftest

        self.srv.close()
        with mock.patch.object(sim, "SITL_PORT", self.port):
            sitl_conftest._require_free_port()   # must not raise


class TestHarnessPortGuard(unittest.TestCase):
    """The scenario harness's port guard, unit-tested without SITL.

    It gates every scenario, so a bug here (refusing a free port, or accepting a
    busy one) would either break all 15 or restore the original silent-collision
    bug. Both are worth pinning cheaply.
    """

    def setUp(self):
        from tests.sitl import harness
        self.h = harness

    def test_free_port_is_accepted(self):
        with mock.patch.object(sim, "_port_listening", return_value=False):
            self.assertTrue(self.h.port_is_free(timeout=1.0))

    def test_occupied_port_is_refused(self):
        with mock.patch.object(sim, "_port_listening", return_value=True):
            self.assertFalse(self.h.port_is_free(timeout=0.2))

    def test_harness_keeps_no_second_copy_of_the_port(self):
        """The original bug: harness.py hardcoded tcp:127.0.0.1:5760 while
        sim.py separately hardcoded 5760, so parameterising one silently left
        the other behind. There must be exactly one source."""
        src = inspect.getsource(self.h)
        self.assertNotIn("5760", src,
                         "harness.py must not hardcode a port — ask the backend")

    def test_contention_message_names_the_cause_and_the_fix(self):
        """This string is the entire value of the guard: the failure it replaces
        was a bare connection error that read like a code regression."""
        msg = self.h.port_contention_message()
        self.assertIn(str(sim.SITL_PORT), msg)
        self.assertIn("SITL_INSTANCE", msg)
        self.assertIn("parallel", msg.lower())

    def test_conn_comes_from_the_backend_not_a_constant(self):
        client = TestClient(app)
        self.assertEqual(self.h.sitl_conn(client), sim.SITL_CONN)

    def test_conn_fails_loudly_if_the_backend_stops_reporting_it(self):
        """A silently-missing connection_string must not degrade into some
        default port — that is the drift this whole change removes."""

        class _NoConnClient:
            def get(self, _url):
                return mock.Mock(status_code=200, text="{}",
                                 json=lambda: {"available": True,
                                               "running": False})

        with self.assertRaises(AssertionError):
            self.h.sitl_conn(_NoConnClient())


class TestSimParamRanges(unittest.TestCase):
    def test_fault_params_are_range_guarded(self):
        ok, _ = param_meta.validate("SIM_BATT_VOLTAGE", 999)
        self.assertFalse(ok)
        ok, _ = param_meta.validate("SIM_GPS1_ENABLE", 2)
        self.assertFalse(ok)
        ok, _ = param_meta.validate("SIM_GPS_DISABLE", 1)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()

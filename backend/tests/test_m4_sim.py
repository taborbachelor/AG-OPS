"""M4 unit tests: sim-router fault injection + GCS heartbeat suppression.

Pure-fake coverage of the new surface so `pytest` (no SITL) still proves the
plumbing; the flight-truth of each fault lives in tests/sitl/ scenarios.
"""
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app import param_meta
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

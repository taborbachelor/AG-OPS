"""M3 regression suite: parameter engine.

Covers metadata validation, the cache, full sync with missing-index re-request,
batch-set with rollback, and change tracking — using the same faithful
ArduPilot-param-store fake as the M1b suite.
"""
import time
import unittest

from pymavlink import mavutil

from app import param_meta
from app.vehicle_manager import VehicleManager
from tests.test_m1b_params import FakeParamConn, make_vm, REAL32, INT8

INT32 = mavutil.mavlink.MAV_PARAM_TYPE_INT32


class ValidationTests(unittest.TestCase):
    def test_finite_required(self):
        ok, err = param_meta.validate("X", float("nan"))
        self.assertFalse(ok)
        self.assertIn("finite", err)

    def test_int_param_rejects_fraction(self):
        ok, err = param_meta.validate("FENCE_ACTION", 3.5, INT8)
        self.assertFalse(ok)
        self.assertIn("integer", err)

    def test_int_type_range(self):
        ok, err = param_meta.validate("SOME_INT8", 200, INT8)  # INT8 max 127
        self.assertFalse(ok)
        self.assertIn("storage range", err)

    def test_curated_range(self):
        self.assertFalse(param_meta.validate("FENCE_RADIUS", 0)[0])       # < 1
        self.assertFalse(param_meta.validate("FENCE_RADIUS", 999999)[0])  # > 50000
        self.assertTrue(param_meta.validate("FENCE_RADIUS", 2500, REAL32)[0])

    def test_unknown_param_only_checks_finite(self):
        self.assertTrue(param_meta.validate("WHATEVER", 123456)[0])


class SetValidationTests(unittest.TestCase):
    def test_set_param_rejects_before_sending(self):
        conn = FakeParamConn({"FENCE_ACTION": (1, INT8)})
        vm = make_vm(conn)
        r = vm.set_param("FENCE_ACTION", 99)  # range 0..6
        self.assertTrue(r.get("rejected"))
        self.assertFalse(r["verified"])
        # Nothing was sent to the vehicle.
        self.assertFalse([c for c in conn.mav.calls if c[0] == "param_set_send"])

    def test_valid_write_uses_cached_type_for_int_check(self):
        conn = FakeParamConn({"FENCE_ACTION": (1, INT8)})
        vm = make_vm(conn)
        vm._cache_put("FENCE_ACTION", 1, INT8)
        self.assertTrue(vm.set_param("FENCE_ACTION", 3)["verified"])
        self.assertTrue(vm.set_param("FENCE_ACTION", 2.5)["rejected"])  # cached INT8


class CacheTests(unittest.TestCase):
    def test_verified_write_updates_cache(self):
        conn = FakeParamConn({"WP_RADIUS": (30.0, REAL32)})
        vm = make_vm(conn)
        vm.set_param("WP_RADIUS", 45)
        self.assertEqual(vm.cached_value("WP_RADIUS"), 45.0)
        self.assertEqual(vm.cached_type("WP_RADIUS"), REAL32)


class SyncTests(unittest.TestCase):
    def test_full_sync_populates_cache(self):
        conn = FakeParamConn({"A": (1.0, REAL32), "B": (2, INT8), "C": (3.0, REAL32)})
        vm = make_vm(conn)
        params = vm.get_all_params(timeout=3)
        self.assertEqual(params, {"A": 1.0, "B": 2.0, "C": 3.0})
        self.assertTrue(vm._param_sync["synced"])
        self.assertEqual(vm._param_sync["total"], 3)

    def test_sync_regapfills_missing_index(self):
        # The list stream drops index 1; gap-fill must re-request and recover it.
        conn = FakeParamConn({"A": (1.0, REAL32), "B": (2.0, REAL32), "C": (3.0, REAL32)},
                             drop_from_list={1})
        vm = make_vm(conn)
        params = vm.get_all_params(timeout=3)
        self.assertEqual(set(params), {"A", "B", "C"})  # B recovered via gap-fill
        self.assertTrue(vm._param_sync["synced"])


class AtomicTests(unittest.TestCase):
    def test_atomic_all_succeed(self):
        conn = FakeParamConn({"FENCE_RADIUS": (300.0, REAL32), "FENCE_ENABLE": (0, INT8)})
        vm = make_vm(conn)
        res = vm.set_params_atomic({"FENCE_RADIUS": 2500, "FENCE_ENABLE": 1})
        self.assertTrue(res["ok"])
        self.assertEqual(conn.store["FENCE_RADIUS"][0], 2500.0)

    def test_atomic_rolls_back_on_failure(self):
        # FENCE_ENABLE write fails (FC won't store it) -> FENCE_RADIUS, already
        # applied, must be rolled back to 300.
        conn = FakeParamConn(
            {"FENCE_RADIUS": (300.0, REAL32), "FENCE_ENABLE": (0, INT8)},
            transform={"FENCE_ENABLE": 5})  # stores 5, not the requested 1
        vm = make_vm(conn)
        res = vm.set_params_atomic({"FENCE_RADIUS": 2500, "FENCE_ENABLE": 1})
        self.assertFalse(res["ok"])
        self.assertEqual(res["failed"], "FENCE_ENABLE")
        self.assertIn("FENCE_RADIUS", res["rolled_back"])
        self.assertTrue(res["rollback_ok"])
        # Rolled back to its previous value on the vehicle.
        self.assertEqual(conn.store["FENCE_RADIUS"][0], 300.0)

    def test_atomic_rejects_invalid_without_sending(self):
        conn = FakeParamConn({"FENCE_RADIUS": (300.0, REAL32)})
        vm = make_vm(conn)
        res = vm.set_params_atomic({"FENCE_RADIUS": 999999})  # out of range
        self.assertFalse(res["ok"])
        self.assertEqual(res["rejected"], "FENCE_RADIUS")
        self.assertFalse([c for c in conn.mav.calls if c[0] == "param_set_send"])
        self.assertEqual(conn.store["FENCE_RADIUS"][0], 300.0)  # untouched


class SnapshotTests(unittest.TestCase):
    def test_param_sync_in_snapshot(self):
        vm = VehicleManager()
        vm._maybe_log = lambda: None
        snap = vm.snapshot()
        self.assertIn("param_sync", snap)
        self.assertFalse(snap["param_sync"]["synced"])


if __name__ == "__main__":
    unittest.main()

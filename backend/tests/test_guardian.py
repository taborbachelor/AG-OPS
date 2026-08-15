"""Guardian layer unit tests: monitor rules, emergency state machine, the
runner's latch/override/give-up behavior, and the /api/safety/guardian API.
"""
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app import guardian
from app.guardian import GuardianConfig, derive_state, evaluate
from app.main import app
from app.vehicle_manager import VehicleManager, vehicle_manager


def _telem(**over):
    t = {"armed": True, "mode": "AUTO", "lat": 39.91, "lon": -95.80,
         "home_lat": 39.9042, "home_lon": -95.7997, "altitude": 60.0,
         "groundspeed": 18.0, "airspeed": 18.0,
         "battery_voltage": 12.4, "battery_current": 12.0,
         "battery_consumed_mah": 500.0, "gps_fix": 3, "gps_satellites": 10,
         "link_level": "good"}
    t.update(over)
    return t


def _eval(cfg=None, t=None, mem=None, now=1000.0):
    return evaluate(cfg or GuardianConfig(), t or _telem(),
                    mem if mem is not None else guardian.default_memory(), now)


class TestMonitors(unittest.TestCase):
    def test_disarmed_bench_vehicle_is_not_an_emergency(self):
        res = _eval(t=_telem(armed=False, battery_voltage=0.0, gps_fix=0,
                             gps_satellites=0))
        self.assertEqual(res["warnings"], [])
        self.assertIsNone(res["action"])

    def test_healthy_flight_is_normal(self):
        res = _eval()
        self.assertEqual(res["warnings"], [])
        self.assertIsNone(res["action"])
        self.assertTrue(all(m["ok"] for m in res["monitors"].values()))

    def test_battery_warn_before_rtl(self):
        res = _eval(t=_telem(battery_voltage=10.6))
        self.assertIn("battery", " ".join(res["warnings"]))
        self.assertIsNone(res["action"], "warn threshold must not command RTL")

    def test_battery_rtl_requires_sustained_low(self):
        cfg = GuardianConfig()
        mem = guardian.default_memory()
        t = _telem(battery_voltage=10.2)
        res = evaluate(cfg, t, mem, now=1000.0)
        self.assertIsNone(res["action"], "first low sample must only start the clock")
        res = evaluate(cfg, t, mem, now=1000.0 + cfg.batt_low_s + 1)
        self.assertEqual((res["action"], res["source"]), ("rtl", "battery"))
        self.assertIn("battery", res["reason"])

    def test_battery_recovery_resets_the_clock(self):
        cfg = GuardianConfig()
        mem = guardian.default_memory()
        evaluate(cfg, _telem(battery_voltage=10.2), mem, now=1000.0)
        evaluate(cfg, _telem(battery_voltage=12.0), mem, now=1005.0)  # recovers
        res = evaluate(cfg, _telem(battery_voltage=10.2), mem,
                       now=1000.0 + cfg.batt_low_s + 5)
        self.assertIsNone(res["action"], "recovery must reset the debounce")

    def test_gps_loss_warns_but_never_rtls_by_default(self):
        res = _eval(t=_telem(gps_fix=1))
        self.assertIn("GPS fix lost", " ".join(res["warnings"]))
        self.assertIsNone(res["action"],
                          "RTL without GPS cannot navigate — warn only")

    def test_gps_rtl_only_when_explicitly_configured(self):
        res = _eval(cfg=GuardianConfig(gps_action="rtl"), t=_telem(gps_fix=1))
        self.assertEqual((res["action"], res["source"]), ("rtl", "gps"))

    def test_thin_sats_warns(self):
        res = _eval(t=_telem(gps_satellites=4))
        self.assertIn("sats", " ".join(res["warnings"]))
        self.assertIsNone(res["action"])

    def test_link_poor_warns(self):
        res = _eval(t=_telem(link_level="poor"))
        self.assertIn("link", " ".join(res["warnings"]))

    def test_margin_math_and_warning(self):
        # 2200mAh pack, 1700 consumed -> 500mAh left at 12A = 150s of battery.
        # 3km from home at 20m/s = 150s home + 90s reserve -> margin -90s.
        cfg = GuardianConfig(pack_capacity_mah=2200)
        t = _telem(battery_consumed_mah=1700.0, battery_current=12.0,
                   groundspeed=20.0,
                   lat=39.9042 + 3000.0 / 111320.0, lon=-95.7997)
        res = _eval(cfg=cfg, t=t)
        m = res["monitors"]["rtl_margin"]
        self.assertAlmostEqual(m["time_left_s"], 150.0, delta=2)
        self.assertAlmostEqual(m["time_home_s"], 150.0, delta=2)
        self.assertFalse(m["ok"])
        self.assertIn("margin", " ".join(res["warnings"]))
        self.assertIsNone(res["action"], "margin default action is warn")

    def test_margin_rtl_when_configured_and_sustained(self):
        cfg = GuardianConfig(pack_capacity_mah=2200, margin_action="rtl",
                             margin_low_s=5.0)
        t = _telem(battery_consumed_mah=1700.0, battery_current=12.0,
                   groundspeed=20.0,
                   lat=39.9042 + 3000.0 / 111320.0, lon=-95.7997)
        mem = guardian.default_memory()
        self.assertIsNone(evaluate(cfg, t, mem, 1000.0)["action"])
        res = evaluate(cfg, t, mem, 1006.0)
        self.assertEqual((res["action"], res["source"]), ("rtl", "margin"))

    def test_margin_unknown_without_capacity(self):
        res = _eval()  # default pack_capacity_mah=0
        self.assertIsNone(res["monitors"]["rtl_margin"]["margin_s"])

    def test_disabled_guardian_never_acts(self):
        cfg = GuardianConfig(enabled=False, batt_low_s=0.0)
        res = _eval(cfg=cfg, t=_telem(battery_voltage=9.5))
        self.assertIsNone(res["action"])

    def test_ekf_unhealthy_warns_but_default_action_is_warn(self):
        res = _eval(t=_telem(ekf_healthy=False, ekf_flags=0))
        self.assertIn("EKF unhealthy", " ".join(res["warnings"]))
        self.assertIsNone(res["action"],
                          "an untrustworthy position solution must not "
                          "auto-command a navigate-home action by default")

    def test_ekf_rtl_only_when_explicitly_configured(self):
        res = _eval(cfg=GuardianConfig(ekf_action="rtl"),
                   t=_telem(ekf_healthy=False))
        self.assertEqual((res["action"], res["source"]), ("rtl", "ekf"))

    def test_ekf_variance_warns_without_flipping_unhealthy(self):
        # Below the flags' own threshold (still "healthy") but past the
        # guardian's earlier variance heads-up.
        res = _eval(t=_telem(ekf_healthy=True, ekf_pos_var=0.75))
        self.assertIn("variance rising", " ".join(res["warnings"]))
        self.assertIsNone(res["action"], "variance warning must never itself RTL")

    def test_ekf_healthy_flight_has_no_ekf_warning(self):
        res = _eval()  # _telem() doesn't set ekf_* -> defaults to healthy
        self.assertTrue(res["monitors"]["ekf"]["ok"])

    def test_vibration_high_warns_after_sustain_then_can_rtl(self):
        cfg = GuardianConfig(vibe_action="rtl", vibe_sustained_s=5.0)
        mem = guardian.default_memory()
        t = _telem(vibration_x=45.0)
        res = evaluate(cfg, t, mem, now=1000.0)
        self.assertIn("vibration high", " ".join(res["warnings"]))
        self.assertIsNone(res["action"], "first high sample must only start the clock")
        res = evaluate(cfg, t, mem, now=1006.0)
        self.assertEqual((res["action"], res["source"]), ("rtl", "vibration"))

    def test_vibration_recovery_resets_the_clock(self):
        cfg = GuardianConfig(vibe_action="rtl", vibe_sustained_s=5.0)
        mem = guardian.default_memory()
        evaluate(cfg, _telem(vibration_z=45.0), mem, now=1000.0)
        evaluate(cfg, _telem(vibration_z=5.0), mem, now=1002.0)  # recovers
        res = evaluate(cfg, _telem(vibration_z=45.0), mem, now=1006.0)
        self.assertIsNone(res["action"], "recovery must reset the debounce")

    def test_clip_growth_measured_from_arm_time_baseline(self):
        # Non-zero clip counts already present at connect (cumulative since
        # boot, not since arm) must not themselves read as new clipping.
        mem = guardian.default_memory()
        cfg = GuardianConfig(vibe_clip_warn=3)
        baseline = _telem(clip_0=50, clip_1=0, clip_2=0)
        res = evaluate(cfg, baseline, mem, now=1000.0)
        self.assertTrue(res["monitors"]["vibration"]["ok"],
                        "pre-existing boot-time clips must not read as new")
        grown = _telem(clip_0=54, clip_1=0, clip_2=0)
        res = evaluate(cfg, grown, mem, now=1001.0)
        self.assertEqual(res["monitors"]["vibration"]["new_clips"], 4)
        self.assertFalse(res["monitors"]["vibration"]["ok"])

    def test_disabled_guardian_never_acts_on_vibration_either(self):
        cfg = GuardianConfig(enabled=False, vibe_action="rtl", vibe_sustained_s=0.0)
        res = _eval(cfg=cfg, t=_telem(vibration_x=90.0))
        self.assertIsNone(res["action"])

    def test_low_airspeed_on_the_ground_is_not_a_stall_warning(self):
        # Taxiing / mid-takeoff-roll: armed, airspeed ~0, but not airborne yet.
        res = _eval(t=_telem(altitude=0.5, airspeed=0.0))
        self.assertTrue(res["monitors"]["airspeed"]["ok"])
        self.assertNotIn("airspeed", " ".join(res["warnings"]))

    def test_low_airspeed_while_airborne_warns(self):
        res = _eval(t=_telem(altitude=60.0, airspeed=6.0))
        self.assertIn("airspeed low", " ".join(res["warnings"]))
        self.assertIn("stall risk", " ".join(res["warnings"]))
        self.assertIsNone(res["action"], "warn threshold must not command RTL")

    def test_airspeed_rtl_requires_sustained_low_and_explicit_config(self):
        cfg = GuardianConfig(airspeed_action="rtl", airspeed_low_s=3.0)
        mem = guardian.default_memory()
        t = _telem(altitude=60.0, airspeed=5.0)
        self.assertIsNone(evaluate(cfg, t, mem, 1000.0)["action"])
        res = evaluate(cfg, t, mem, 1004.0)
        self.assertEqual((res["action"], res["source"]), ("rtl", "airspeed"))

    def test_airspeed_recovery_resets_the_clock(self):
        cfg = GuardianConfig(airspeed_action="rtl", airspeed_low_s=3.0)
        mem = guardian.default_memory()
        evaluate(cfg, _telem(altitude=60.0, airspeed=5.0), mem, now=1000.0)
        evaluate(cfg, _telem(altitude=60.0, airspeed=15.0), mem, now=1001.0)  # recovers
        res = evaluate(cfg, _telem(altitude=60.0, airspeed=5.0), mem, now=1005.0)
        self.assertIsNone(res["action"], "recovery must reset the debounce")

    def test_disabled_guardian_never_acts_on_airspeed_either(self):
        cfg = GuardianConfig(enabled=False, airspeed_action="rtl", airspeed_low_s=0.0)
        res = _eval(cfg=cfg, t=_telem(altitude=60.0, airspeed=2.0))
        self.assertIsNone(res["action"])


class TestStateMachine(unittest.TestCase):
    def test_transitions(self):
        s = derive_state(guardian.NORMAL, True, "AUTO", [], False, False)
        self.assertEqual(s, guardian.NORMAL)
        s = derive_state(s, True, "AUTO", ["battery low"], False, False)
        self.assertEqual(s, guardian.WARNING)
        s = derive_state(s, True, "AUTO", ["battery low"], True, False)
        self.assertEqual(s, guardian.RTL_REQUESTED)
        s = derive_state(s, True, "RTL", ["battery low"], True, False)
        self.assertEqual(s, guardian.RTL_ACTIVE)
        s = derive_state(s, True, "AUTO", [], False, True)
        self.assertEqual(s, guardian.LANDING)
        s = derive_state(s, False, "AUTO", [], False, False)
        self.assertEqual(s, guardian.DISARMED)
        # Sticky while disarmed; fresh NORMAL is only for a never-flown session.
        s = derive_state(s, False, "MANUAL", [], False, False)
        self.assertEqual(s, guardian.DISARMED)
        self.assertEqual(derive_state(guardian.NORMAL, False, "MANUAL", [],
                                      False, False), guardian.NORMAL)

    def test_rtl_active_reports_reality_whoever_commanded_it(self):
        s = derive_state(guardian.NORMAL, True, "RTL", [], False, False)
        self.assertEqual(s, guardian.RTL_ACTIVE)


class TestRunner(unittest.TestCase):
    def _vm(self):
        vm = VehicleManager()
        vm.guardian_config = GuardianConfig(batt_low_s=0.0)
        vm.telemetry.armed = True
        vm.telemetry.mode = "AUTO"
        vm.telemetry.gps_fix = 3
        vm.telemetry.gps_satellites = 10
        vm.telemetry.battery_voltage = 12.4
        return vm

    def test_tick_commands_rtl_and_latches(self):
        vm = self._vm()
        vm.set_mode = mock.Mock(return_value=True)
        vm._guardian_tick(1000.0)          # healthy: arms the rising edge
        vm.telemetry.battery_voltage = 10.0
        vm._guardian_tick(1001.0)
        vm.set_mode.assert_called_once_with("RTL")
        self.assertEqual(vm.guardian_state()["rtl_source"], "battery")
        self.assertEqual(vm.guardian_state()["state"], guardian.RTL_REQUESTED)
        # Condition persists, already latched: no second command.
        vm._guardian_tick(1002.0)
        vm.set_mode.assert_called_once()

    def test_operator_override_stands_down(self):
        vm = self._vm()
        vm.set_mode = mock.Mock(return_value=True)
        vm._guardian_tick(1000.0)
        vm.telemetry.battery_voltage = 10.0
        vm._guardian_tick(1001.0)          # guardian commands RTL
        vm.telemetry.mode = "FBWA"         # operator takes over
        vm._guardian_tick(1002.0)          # override detected -> standdown
        vm._guardian_tick(1003.0)          # condition persists, must NOT re-fire
        vm.set_mode.assert_called_once()
        self.assertEqual(vm._guardian_mem["standdown_for"], "battery")

    def test_rejected_rtl_gives_up_after_three_attempts(self):
        vm = self._vm()
        vm.set_mode = mock.Mock(return_value=False)
        vm._guardian_tick(1000.0)
        vm.telemetry.battery_voltage = 10.0
        for i in range(5):
            vm._guardian_tick(1001.0 + i)
        self.assertEqual(vm.set_mode.call_count, 3)

    def test_snapshot_carries_guardian(self):
        vm = self._vm()
        self.assertIn("guardian", vm.snapshot())
        self.assertEqual(vm.snapshot()["guardian"]["state"], guardian.NORMAL)


class TestGuardianApi(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self._saved = guardian.config_to_dict(vehicle_manager.guardian_config)

    def tearDown(self):
        vehicle_manager.set_guardian_config(self._saved)

    def test_get_returns_config_and_state(self):
        r = self.client.get("/api/safety/guardian")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("batt_rtl_volt", body["config"])
        self.assertIn("ekf_var_warn", body["config"])
        self.assertIn("vibe_warn_ms2", body["config"])
        self.assertIn("state", body["state"])

    def test_partial_update_touches_only_named_fields(self):
        before = guardian.config_to_dict(vehicle_manager.guardian_config)
        r = self.client.post("/api/safety/guardian",
                             json={"batt_warn_volt": 11.2})
        self.assertEqual(r.status_code, 200)
        after = r.json()["config"]
        self.assertEqual(after["batt_warn_volt"], 11.2)
        self.assertEqual(after["batt_rtl_volt"], before["batt_rtl_volt"])

    def test_inverted_battery_thresholds_rejected(self):
        r = self.client.post("/api/safety/guardian",
                             json={"batt_rtl_volt": 12.0, "batt_warn_volt": 11.0})
        self.assertEqual(r.status_code, 422)

    def test_inverted_airspeed_thresholds_rejected(self):
        r = self.client.post("/api/safety/guardian",
                             json={"airspeed_min_ms": 12.0, "airspeed_warn_ms": 10.0})
        self.assertEqual(r.status_code, 422)

    def test_empty_update_rejected(self):
        r = self.client.post("/api/safety/guardian", json={})
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()

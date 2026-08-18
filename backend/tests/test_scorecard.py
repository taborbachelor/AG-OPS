"""Post-flight scorecard (SPRAY-FLIGHT-SAFETY.md Part 3B).

The point of the scorecard is the NEAR miss: an EKF variance that peaked just
under the warn threshold, or a hazard approach that never breached, leaves no
trace in the event log. Three flights of that in a row is a degradation trend
you want to see before it becomes an incident.
"""
import json
import math
import unittest

from fastapi.testclient import TestClient

from app import guardian
from app.main import app
from app.vehicle_manager import VehicleManager, _new_flight_stats


def _telem(**over):
    t = {"armed": True, "mode": "AUTO", "altitude": 60.0, "airspeed": 18.0,
         "groundspeed": 18.0, "battery_voltage": 12.4, "gps_fix": 3,
         "gps_satellites": 10, "link_level": "good", "roll": 0.0,
         "lat": 39.9042, "lon": -95.7997}
    t.update(over)
    return t


def _res(monitors=None):
    return {"monitors": monitors or {}, "warnings": [], "action": None,
            "reason": None, "source": None}


class TestAccumulator(unittest.TestCase):
    def setUp(self):
        self.vm = VehicleManager()
        self.vm._flight_stats = _new_flight_stats(1000.0)

    def test_extremes_start_unknown_not_zero(self):
        """A scorecard reporting 0 m to the nearest powerline when no rings
        were ever loaded would be a dangerous lie."""
        s = _new_flight_stats(0.0)
        for k in ("max_bank_deg", "min_hazard_dist_m", "max_wind_ms",
                  "min_rtl_margin_s", "min_airspeed_ms"):
            self.assertIsNone(s[k], k)

    def test_tracks_worst_case_across_ticks(self):
        for roll_deg, wind, ekf in ((10, 2.0, 0.1), (55, 9.0, 0.55), (20, 4.0, 0.2)):
            self.vm._accumulate_flight_stats(
                _telem(roll=math.radians(roll_deg), wind_speed=wind,
                       ekf_pos_var=ekf), _res(), 1001.0)
        s = self.vm._flight_stats
        self.assertAlmostEqual(s["max_bank_deg"], 55.0, delta=0.1)
        self.assertAlmostEqual(s["max_wind_ms"], 9.0, delta=0.01)
        self.assertAlmostEqual(s["max_ekf_pos_var"], 0.55, delta=0.01)

    def test_captures_a_near_miss_that_never_warned(self):
        """0.55 against a 0.6 threshold: no warning, no event — but recorded."""
        self.vm._accumulate_flight_stats(
            _telem(ekf_pos_var=0.55), _res({"ekf": {"ok": True}}), 1001.0)
        s = self.vm._flight_stats
        self.assertAlmostEqual(s["max_ekf_pos_var"], 0.55, delta=0.01)
        self.assertEqual(s["warnings"], {}, "a near miss is not a warning")

    def test_minimums_track_closest_approach(self):
        for dist in (40.0, 12.0, 25.0):
            self.vm._accumulate_flight_stats(
                _telem(), _res({"keepout": {"ok": True, "hazard_dist_m": dist}}),
                1001.0)
        self.assertAlmostEqual(self.vm._flight_stats["min_hazard_dist_m"], 12.0)

    def test_warnings_counted_per_episode_not_per_tick(self):
        """Held warnings inflate a tick count; the operator wants 'how many
        times did this happen', not 'for how many seconds'."""
        bad = _res({"bank": {"ok": False}})
        good = _res({"bank": {"ok": True}})
        for r in (bad, bad, bad, good, bad, bad):
            self.vm._accumulate_flight_stats(_telem(), r, 1001.0)
        self.assertEqual(self.vm._flight_stats["warnings"]["bank"], 2)

    def test_counts_each_monitor_separately(self):
        self.vm._accumulate_flight_stats(
            _telem(), _res({"bank": {"ok": False}, "airspeed": {"ok": False}}),
            1001.0)
        self.assertEqual(self.vm._flight_stats["warnings"],
                         {"bank": 1, "airspeed": 1})


class TestWriteOnDisarm(unittest.TestCase):
    def setUp(self):
        self.vm = VehicleManager()

    def test_scorecard_written_next_to_the_log(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "flight_20260818_120000.jsonl"
            path.write_text("{}\n", encoding="utf-8")
            self.vm._log_path = path
            self.vm._flight_stats = _new_flight_stats(1000.0)
            self.vm._accumulate_flight_stats(
                _telem(roll=math.radians(48)), _res({"bank": {"ok": False}}), 1030.0)
            self.vm._write_scorecard()

            card = json.loads(
                path.with_suffix(".scorecard.json").read_text(encoding="utf-8"))
            self.assertAlmostEqual(card["max_bank_deg"], 48.0, delta=0.2)
            self.assertEqual(card["warnings"], {"bank": 1})
            self.assertEqual(card["duration_s"], 30.0)
            self.assertNotIn("_warning_active", card, "working state must not leak")

    def test_no_samples_writes_nothing(self):
        """Arming and immediately disarming is not a flight."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "flight_20260818_120000.jsonl"
            path.write_text("{}\n", encoding="utf-8")
            self.vm._log_path = path
            self.vm._flight_stats = _new_flight_stats(1000.0)
            self.vm._write_scorecard()
            self.assertFalse(path.with_suffix(".scorecard.json").exists())

    def test_write_failure_never_breaks_disarm(self):
        self.vm._log_path = None       # nowhere to write
        self.vm._flight_stats = _new_flight_stats(1000.0)
        self.vm._accumulate_flight_stats(_telem(), _res(), 1010.0)
        self.vm._write_scorecard()      # must not raise
        self.assertIsNone(self.vm._flight_stats, "stats still reset for next flight")


class TestLogsApi(unittest.TestCase):
    def test_missing_scorecard_is_null_not_an_error(self):
        """Flights recorded before scorecards existed, or a flight the backend
        never saw disarm, must still serve their log."""
        from app.routers import logs as logs_router
        client = TestClient(app)
        r = client.get("/api/logs")
        self.assertEqual(r.status_code, 200)
        for row in r.json()["logs"]:
            self.assertIn("has_scorecard", row)


if __name__ == "__main__":
    unittest.main()

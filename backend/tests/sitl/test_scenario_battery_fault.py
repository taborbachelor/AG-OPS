"""Scenario: battery-fault — pack sags below the failsafe threshold in flight.

The vehicle must trigger its battery failsafe and bring itself home (RTL).
ArduPlane latches battery failsafes by design (a tired pack doesn't get
better), so the scenario ends at the RTL verdict — no "recovery" leg.
"""
import pytest

from tests.sitl import harness as h

pytestmark = [pytest.mark.sitl, pytest.mark.slow]

SPEEDUP = 5.0


def test_low_battery_triggers_rtl(client):
    h.launch(client, speedup=SPEEDUP, fresh_eeprom=True)

    # Battery failsafe armed: low -> RTL at 11.0V. GCS failsafe stays OFF
    # (speedup>1 + wall-clock heartbeats would false-trigger it — see harness
    # docstring). BATT_LOW_TIMER (default 10 sim-seconds under threshold)
    # is only ~2 wall-seconds at speedup 5.
    h.set_failsafe(client, batt_low_volt=11.0, batt_low_action=1,
                   batt_crit_volt=9.0, batt_crit_action=1, gcs_enable=0)

    h.takeoff(client, alt=50)
    h.wait_for(client, lambda t: t["armed"], 30, "vehicle armed")
    h.wait_for(client, lambda t: t["altitude"] > 30, 120, "climb-out above 30m")

    healthy = h.telem(client)
    assert healthy["battery_voltage"] > 11.5, \
        f"pack should start healthy, got {healthy['battery_voltage']}V"

    # Sag the pack to 10.2V — below low (11.0), above critical (9.0).
    h.inject_fault(client, "battery", enable=True, value=10.2)
    h.wait_for(client, lambda t: t["battery_voltage"] < 10.8, 60,
               "sagged voltage visible in telemetry")
    h.wait_for(client, lambda t: t["mode"] == "RTL", 120,
               "battery failsafe commands RTL")

    # The injection itself must be in the audit trail (the RTL mode observed
    # above is the authoritative verdict on the failsafe).
    events = h.recent_events(client, 300)
    assert h.has_event(events, "sim", "fault_injected")

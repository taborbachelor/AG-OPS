"""Scenario: guardian — the GCS-side failsafe layer saves the flight itself.

ArduPilot's own battery failsafe is deliberately DISABLED here; the pack sags
below the guardian's threshold and the GUARDIAN must be the one to notice,
record the triggering condition, and command RTL. Proves the second defense
layer end-to-end: monitor -> sustained-condition debounce -> logged trigger ->
verified RTL -> emergency state machine tracking reality.
"""
import pytest

from tests.sitl import harness as h

pytestmark = [pytest.mark.sitl, pytest.mark.slow]

SPEEDUP = 5.0


def _guardian(client) -> dict:
    return h.telem(client).get("guardian") or {}


def test_guardian_commands_rtl_on_battery_sag(client):
    h.launch(client, speedup=SPEEDUP, fresh_eeprom=True)

    # Vehicle-side battery failsafe OFF — if RTL happens, it was the guardian.
    h.set_failsafe(client, batt_low_volt=10.5, batt_low_action=0,
                   batt_crit_volt=8.0, batt_crit_action=0, gcs_enable=0)
    # Guardian: RTL when the pack holds below 10.8V for 3s (wall time — the
    # guardian ticks at 1Hz wall regardless of sim speedup).
    r = client.post("/api/safety/guardian",
                    json={"batt_warn_volt": 11.2, "batt_rtl_volt": 10.8,
                          "batt_action": "rtl", "batt_low_s": 3.0})
    assert r.status_code == 200, r.text

    h.takeoff(client, alt=50)
    h.wait_for(client, lambda t: t["armed"], 30, "vehicle armed")
    h.wait_for(client, lambda t: t["altitude"] > 30, 120, "climb-out above 30m")
    assert _guardian(client).get("state") in ("NORMAL", "WARNING"), \
        f"pre-fault guardian state: {_guardian(client)}"

    # Sag to 10.3V: below the guardian's 10.8 RTL line, above the vehicle's
    # (disabled anyway) thresholds.
    h.inject_fault(client, "battery", enable=True, value=10.3)
    h.wait_for(client, lambda t: t["battery_voltage"] < 10.8, 60,
               "sagged voltage visible")

    # The guardian (not the autopilot) must command RTL after the debounce.
    h.wait_for(client, lambda t: t["mode"] == "RTL", 60,
               "guardian-commanded RTL")
    h.wait_for(client,
               lambda t: (t.get("guardian") or {}).get("state") == "RTL_ACTIVE",
               15, "emergency state machine tracks RTL_ACTIVE")
    g = _guardian(client)
    assert g.get("rtl_source") == "battery", g
    assert "battery" in (g.get("rtl_reason") or ""), g

    # Triggering condition recorded BEFORE the command (directive requirement):
    # both events exist, and the trigger's reason names the condition.
    events = h.recent_events(client, 400)
    assert h.has_event(events, "guardian", "rtl_triggered")
    trig = next(e for e in events if e.get("component") == "guardian"
                and e.get("event") == "rtl_triggered")
    assert "battery" in trig.get("reason", ""), trig
    assert h.has_event(events, "guardian", "state")

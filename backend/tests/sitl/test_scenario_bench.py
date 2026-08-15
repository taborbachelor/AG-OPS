"""Scenario: bench — the internals-installation day, rehearsed end-to-end.

The exact sequence Caleb will run when the electronics go into the airframe:
full param backup -> servo exerciser (with the throttle guard proven) ->
calibration command -> first-flight safety bundle -> restore round-trip.
All against the real firmware, disarmed, on the bench.
"""
import pytest

from tests.sitl import harness as h

pytestmark = [pytest.mark.sitl, pytest.mark.slow]

SPEEDUP = 5.0


def test_bench_day_sequence(client):
    h.launch(client, speedup=SPEEDUP, fresh_eeprom=True)
    # The backup needs the full cache: wait for the on-connect sync to finish.
    h.wait_for(client, lambda t: (t.get("param_sync") or {}).get("synced"),
               120, "full parameter sync")

    # 1. Backup first — every later change must be reversible.
    r = client.get("/api/vehicle/params/backup")
    assert r.status_code == 200, r.text
    backup = r.text
    assert len(backup.splitlines()) > 1000, "suspiciously small backup"
    wp_radius_before = next(
        float(ln.split(",")[1]) for ln in backup.splitlines()
        if ln.startswith("WP_RADIUS,"))

    # 2. Surface exerciser: deflect the aileron through the real pilot path
    # (MANUAL + RC override) and confirm the FC's servo output followed.
    r = client.post("/api/bench/surface",
                    json={"surface": "aileron", "pwm": 1900, "hold_s": 3.0})
    assert r.status_code == 200, r.text
    # The output won't equal the input PWM — RC input scales into the servo's
    # own SERVO1_MIN/MAX range (observed live: 1900 in -> 1820 out). What
    # matters is a hard deflection away from the 1500 trim.
    outs = r.json()["servo_outputs_during_hold"]
    assert outs and outs[0] >= 1750, \
        f"aileron output did not follow the deflection: {outs}"

    # 2b. Aux exerciser: SERVO5 is unassigned on fresh defaults — DO_SET_SERVO
    # drives it directly (this is the future spray-pump channel path).
    r = client.post("/api/bench/servo", json={"channel": 5, "pwm": 1700})
    assert r.status_code == 200, r.text
    h.wait_for(client,
               lambda t: len(t.get("servo_outputs") or []) >= 5
               and abs((t["servo_outputs"][4] or 0) - 1700) < 25,
               30, "aux servo 5 follows DO_SET_SERVO")
    assert client.post("/api/bench/servo/release",
                       json={"channel": 5}).status_code == 200

    # 3. Prop-off guarantees: an autopilot-owned channel refuses DO_SET_SERVO,
    # and the throttle surface refuses without explicit consent.
    r = client.post("/api/bench/servo", json={"channel": 3, "pwm": 1400})
    assert r.status_code == 409, \
        f"assigned-channel guard must hold: {r.status_code} {r.text}"
    r = client.post("/api/bench/surface",
                    json={"surface": "throttle", "pwm": 1300, "hold_s": 1.0})
    assert r.status_code == 409, \
        f"throttle-consent guard must hold: {r.status_code} {r.text}"

    # 4. A calibration command is accepted and audited.
    r = client.post("/api/bench/calibrate", json={"kind": "baro"})
    assert r.status_code == 200, r.text

    # 5. First-flight bundle: preview, then atomic apply; the fence advisory
    # on the preflight checklist must flip green.
    pf_fence = next(c for c in client.get("/api/safety/preflight").json()["checks"]
                    if c["id"] == "fence")
    assert not pf_fence["ok"], "fresh eeprom should start with fence off"
    r = client.post("/api/bench/first-flight-params",
                    json={"cells": 3, "apply": True})
    assert r.status_code == 200, r.text
    assert r.json()["verified"]
    pf_fence = next(c for c in client.get("/api/safety/preflight").json()["checks"]
                    if c["id"] == "fence")
    assert pf_fence["ok"], "bundle must enable the fence"
    fs = client.get("/api/safety/failsafe").json()
    assert abs(fs["batt_low_volt"] - 10.5) < 0.01, fs

    # 6. Restore round-trip: the pre-bundle backup puts WP_RADIUS back.
    r = client.post("/api/vehicle/params/restore", json={"content": backup})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "WP_RADIUS" in body["written"], body
    assert not body["failed"], body
    assert abs(client.get("/api/vehicle/params?cached=true").json()
               ["params"]["WP_RADIUS"] - wp_radius_before) < 0.01

    # The whole bench session is in the audit trail.
    events = h.recent_events(client, 400)
    assert h.has_event(events, "bench", "surface_test")
    assert h.has_event(events, "command", "servo_test")
    assert h.has_event(events, "bench", "first_flight_bundle_applied")
    assert h.has_event(events, "bench", "params_restored")

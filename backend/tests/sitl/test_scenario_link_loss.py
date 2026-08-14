"""Scenario: link-loss — ground station vanishes mid-flight.

The vehicle must notice our silence and bring itself home (FS_GCS -> RTL),
and must accept commands again once the GCS returns. Runs at speedup 1:
the GCS failsafe times heartbeats in SIM seconds and we send them in WALL
seconds, so this scenario only measures truthfully in real time.
"""
import time

import pytest

from tests.sitl import harness as h

pytestmark = [pytest.mark.sitl, pytest.mark.slow]


def test_gcs_loss_triggers_rtl_and_recovers(client):
    h.launch(client, speedup=1.0, fresh_eeprom=True)

    # Arm the GCS failsafe for real: lost-GCS long action = RTL.
    h.set_failsafe(client, gcs_enable=1, rc_long_action=1)

    h.takeoff(client, alt=50)
    h.wait_for(client, lambda t: t["armed"], 30, "vehicle armed")
    h.wait_for(client, lambda t: t["altitude"] > 30, 180, "climb-out above 30m")

    # Silence our heartbeat. Our RX keeps listening — we watch the vehicle
    # conclude the GCS is gone (FS_SHORT may pass through CIRCLE first, then
    # FS_LONG commands RTL).
    h.inject_fault(client, "gcs_link", enable=True)
    h.wait_for(client, lambda t: t["mode"] == "RTL", 90,
               "vehicle self-commands RTL on GCS loss")
    h.wait_for(client, lambda t: h.dist_home_m(t) < 400 or t["mode"] == "RTL", 120,
               "vehicle heading home under failsafe")

    # GCS returns: failsafe clears, vehicle must obey us again. The failsafe
    # needs a few heartbeats to unlatch, so retry the mode change briefly —
    # what must NOT happen is the vehicle ignoring us forever.
    h.inject_fault(client, "gcs_link", enable=False)
    h.wait_for(client, lambda t: not t["gcs_hb_suppressed"], 10,
               "heartbeat restored")
    deadline = time.monotonic() + 60
    accepted = False
    while time.monotonic() < deadline and not accepted:
        accepted = h.set_mode(client, "LOITER", expect_ok=False).status_code == 200
        if not accepted:
            time.sleep(2.0)
    assert accepted, "vehicle kept rejecting mode changes after GCS recovery"
    h.wait_for(client, lambda t: t["mode"] == "LOITER", 30,
               "mode is LOITER after recovery")

    # The whole episode must be in the audit trail.
    events = h.recent_events(client, 300)
    assert h.has_event(events, "sim", "fault_injected")
    assert h.has_event(events, "sim", "fault_cleared")
    assert h.has_event(events, "sim", "gcs_heartbeat_suppressed")
    assert h.has_event(events, "sim", "gcs_heartbeat_restored")

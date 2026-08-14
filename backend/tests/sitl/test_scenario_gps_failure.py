"""Scenario: gps-failure — GPS dies in flight, then comes back.

The vehicle keeps flying (ArduPlane dead-reckons), our telemetry must tell
the truth about the failure (fix collapses / EKF degrades), the backend link
must ride through it unshaken, and the fix must recover when GPS returns.
"""
import pytest

from tests.sitl import harness as h

pytestmark = [pytest.mark.sitl, pytest.mark.slow]

SPEEDUP = 5.0


def test_gps_loss_is_surfaced_and_recovers(client):
    h.launch(client, speedup=SPEEDUP, fresh_eeprom=True)
    h.takeoff(client, alt=50)
    h.wait_for(client, lambda t: t["armed"], 30, "vehicle armed")
    h.wait_for(client, lambda t: t["altitude"] > 30, 120, "climb-out above 30m")

    # Kill the GPS. Truth requirement: the operator must SEE it.
    h.inject_fault(client, "gps", enable=True)
    h.wait_for(client, lambda t: t["gps_fix"] < 3 or not t["ekf_healthy"], 90,
               "GPS failure visible in telemetry (fix lost or EKF degraded)")

    # The backend link itself must not flinch: still connected, still READY
    # (or briefly DEGRADED) — never LOST, never a crashed telemetry loop.
    snap = h.telem(client)
    assert snap["connected"], "backend dropped the link during a GPS-only fault"
    assert snap["link_state"] in ("READY", "DEGRADED"), snap["link_state"]
    assert snap["armed"], "vehicle should still be flying on dead-reckoning"

    # GPS returns: fix and EKF must recover.
    h.inject_fault(client, "gps", enable=False)
    h.wait_for(client, lambda t: t["gps_fix"] >= 3, 180, "3D fix reacquired")
    h.wait_for(client, lambda t: t["ekf_healthy"], 180, "EKF healthy again")

    events = h.recent_events(client, 300)
    assert h.has_event(events, "sim", "fault_injected")
    assert h.has_event(events, "sim", "fault_cleared")

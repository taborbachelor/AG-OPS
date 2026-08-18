"""Scenario: ekf-variance — the EKF drifts while its health flags stay GOOD.

This is the monitor's whole reason to exist. The plain `gps` fault kills the
fix outright, which flips ekf_healthy False and takes the guardian's
`ekf_bad` branch. The variance monitor covers the case BEFORE that: the
solution is still "healthy" by the flags, but its reported variance is
climbing — a degrading fix the operator should hear about while there is
still time to do something.

Fault: SIM_GPS1_HNSE (GPS horizontal noise). Measured on this SITL 2026-08-18
at 80 m in AUTO — 10 m of noise drives pos_horiz_variance to ~2-4 against the
0.6 warn threshold, with ekf_healthy still True throughout.

Part 3C of SPRAY-FLIGHT-SAFETY.md: this monitor shipped in c401628 with unit
tests only. This is the first thing that drives it from a real telemetry
stream.
"""
import time

import pytest

from tests.sitl import harness as h

pytestmark = [pytest.mark.sitl, pytest.mark.slow]

SPEEDUP = 5.0
# Wall seconds to let an injected noise level actually reach the EKF solution.
# The guardian ticks at 1 Hz WALL time regardless of sim speedup, so this is
# a wall-clock number and does not scale with SPEEDUP.
SETTLE_S = 6.0
# Must match GuardianConfig.ekf_var_warn — the scenario asserts the monitor
# agrees with its own configured threshold, so it reads the number it judges by.
VAR_WARN = 0.6


def _ekf(client) -> dict:
    return h.guardian(client).get("monitors", {}).get("ekf", {})


def test_ekf_variance_warns_before_the_flags_go_unhealthy(client):
    h.launch(client, speedup=SPEEDUP, fresh_eeprom=True)
    h.set_failsafe(client, gcs_enable=0)
    h.takeoff(client, alt=80)
    h.wait_for(client, lambda t: t["armed"], 30, "vehicle armed")
    h.wait_for(client, lambda t: t["altitude"] > 60, 180, "climb-out above 60m")

    clean = _ekf(client)
    assert clean.get("ok") is True, f"pre-fault EKF monitor not clean: {clean}"
    assert clean.get("healthy") is True, clean
    baseline_var = max(clean.get("pos_var", 0.0), clean.get("vel_var", 0.0))
    assert baseline_var < VAR_WARN, f"baseline already at threshold: {clean}"

    # Mild noise first. This is a CONSISTENCY check, not a "stays quiet" check:
    # asserting the sim keeps variance under a threshold would be flaky, so
    # instead require that whatever the monitor reports, its ok/not-ok verdict
    # matches its own configured threshold on that same live data.
    h.inject_fault(client, "gps_noise", enable=True, value=2.0)
    # A real settle, not a wait_for with a true predicate: the EKF needs time
    # to actually respond to the noise before its verdict means anything.
    time.sleep(SETTLE_S)
    mild = _ekf(client)
    over = (mild.get("pos_var", 0.0) >= VAR_WARN
            or mild.get("vel_var", 0.0) >= VAR_WARN)
    assert mild.get("ok") is (not over), \
        f"EKF monitor disagrees with its own {VAR_WARN} threshold: {mild}"

    # Now enough noise to cross it for real.
    h.inject_fault(client, "gps_noise", enable=True, value=10.0)
    snap = h.wait_warning(client, "EKF variance rising", 90,
                          "guardian names the rising EKF variance")

    mon = (snap.get("guardian") or {}).get("monitors", {}).get("ekf", {})
    assert max(mon.get("pos_var", 0.0), mon.get("vel_var", 0.0)) >= VAR_WARN, mon
    assert mon.get("ok") is False, mon
    # THE POINT OF THIS SCENARIO: the flags never went unhealthy, so this
    # warning came from the variance branch and not from `ekf_bad`.
    assert mon.get("healthy") is True, \
        f"EKF went outright unhealthy — this proves the wrong branch: {mon}"
    assert snap["gps_fix"] >= 3, \
        f"fix was lost, so this is the gps-failure scenario again: {snap['gps_fix']}"
    assert (snap.get("guardian") or {}).get("state") == "WARNING", \
        (snap.get("guardian") or {}).get("state")
    # Warn-only monitor by default: it must NOT have commanded anything.
    assert (snap.get("guardian") or {}).get("rtl_source") is None, snap["guardian"]
    assert snap["mode"] != "RTL", "EKF variance is warn-only; nothing may command RTL"

    # Clear the noise: the monitor must recover, not latch.
    h.inject_fault(client, "gps_noise", enable=False)
    h.wait_for(client,
               lambda t: ((t.get("guardian") or {}).get("monitors", {})
                          .get("ekf", {}).get("ok") is True),
               120, "EKF monitor recovers once the noise is removed")

    events = h.recent_events(client, 400)
    assert h.has_event(events, "sim", "fault_injected")
    assert h.has_event(events, "sim", "fault_cleared")
    assert h.has_event(events, "guardian", "state")

"""Scenario: airspeed-stall — the pitot lies low and the guardian says so.

Fault: SIM_ARSPD_FAIL pins the airspeed sensor to a stuck low reading, the
signature of a blocked or iced pitot. That is the failure this monitor is for:
airspeed is what stands between a spray pass and a stall-spin, and at spray
altitude a stall-spin is unrecoverable.

TWO THINGS MEASURED ON THIS SITL (2026-08-18) THAT SHAPE THE ASSERTIONS:

1. ArduPilot runs its own airspeed-health check and DISABLES a sensor it
   judges implausible about 1.4 s in ("Airspeed sensor 1 failure. Disabling"),
   then re-enables it when the reading looks sane again. So the reported
   airspeed OSCILLATES between the faulted value and synthetic/recovered
   values — it does not sit low. This scenario therefore proves the WARNING
   (the monitor's default action), not a sustained-low RTL escalation.

2. That autopilot statustext is asserted on purpose: it is independent
   corroboration that the fault reached the vehicle and was real, rather than
   something we only told ourselves in our own telemetry parser.

Part 3C of SPRAY-FLIGHT-SAFETY.md — the monitor shipped in c401628 with unit
tests only.
"""
import pytest

from tests.sitl import harness as h

pytestmark = [pytest.mark.sitl, pytest.mark.slow]

SPEEDUP = 5.0
STUCK_AIRSPEED_MS = 4.0   # below GuardianConfig.airspeed_min_ms (8.0)
WARN_MS = 10.0            # GuardianConfig.airspeed_warn_ms


def _airspeed_mon(client) -> dict:
    return h.guardian(client).get("monitors", {}).get("airspeed", {})


def test_stuck_pitot_raises_the_stall_margin_warning(client):
    h.launch(client, speedup=SPEEDUP, fresh_eeprom=True)
    h.set_failsafe(client, gcs_enable=0)
    h.takeoff(client, alt=80)
    h.wait_for(client, lambda t: t["armed"], 30, "vehicle armed")
    h.wait_for(client, lambda t: t["altitude"] > 60, 180, "climb-out above 60m")

    clean = _airspeed_mon(client)
    assert clean.get("ok") is True, f"pre-fault airspeed monitor unhappy: {clean}"
    # Gating on airborne (not merely armed) is deliberate in the monitor —
    # a taxiing plane legitimately reads near zero. Prove we are past that gate,
    # or the warning below would be untrustworthy.
    assert clean.get("airborne") is True, clean
    assert clean.get("airspeed", 0.0) > WARN_MS, \
        f"cruise airspeed should be well above the warn line: {clean}"

    h.inject_fault(client, "airspeed", enable=True, value=STUCK_AIRSPEED_MS)
    snap = h.wait_warning(client, "airspeed low", 60,
                          "guardian names the low airspeed")

    mon = (snap.get("guardian") or {}).get("monitors", {}).get("airspeed", {})
    assert mon.get("airborne") is True, mon
    assert mon.get("airspeed", 99.0) < WARN_MS, mon
    assert mon.get("ok") is False, mon
    # Below the stall floor, the operator-facing string must say so by name.
    warning = next(w for w in (snap.get("guardian") or {}).get("warnings", [])
                   if "airspeed low" in w.lower())
    assert "stall risk" in warning.lower(), \
        f"warning did not name the stall risk: {warning!r}"
    assert (snap.get("guardian") or {}).get("state") == "WARNING", snap["guardian"]
    # Warn-only by default — nothing may have commanded RTL off the back of it.
    assert (snap.get("guardian") or {}).get("rtl_source") is None, snap["guardian"]

    # Independent corroboration: the AUTOPILOT also rejected this sensor.
    # (See note 2 in the module docstring.)
    h.wait_for(
        client,
        lambda t: any("airspeed" in str(e.get("text", "")).lower()
                      and "fail" in str(e.get("text", "")).lower()
                      for e in h.recent_events(client, 400)
                      if e.get("event") == "statustext"),
        60, "ArduPilot reports its own airspeed sensor failure")

    # Clear it: the sensor recovers and the monitor must clear with it.
    h.inject_fault(client, "airspeed", enable=False)
    h.wait_for(client,
               lambda t: ((t.get("guardian") or {}).get("monitors", {})
                          .get("airspeed", {}).get("ok") is True),
               120, "airspeed monitor recovers once the pitot reads true")

    events = h.recent_events(client, 400)
    assert h.has_event(events, "sim", "fault_injected")
    assert h.has_event(events, "sim", "fault_cleared")

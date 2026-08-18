"""Scenario: bank-angle — a real turn, a real roll angle, a real warning.

No fault injection needed and none used: this airframe genuinely banks past
ArduPlane's own ROLL_LIMIT_DEG during ordinary loiter and RTL turns (measured
2026-08-18: 50-65 deg peaks). The monitor's default threshold IS
ROLL_LIMIT_DEG, so simply flying the aircraft exercises it against live
ATTITUDE telemetry.

That the default fires during routine turns is a real finding about the
aircraft, not a badly-chosen threshold — a 60 deg bank raises stall speed ~41%,
and at the 10-25 m spray altitude this platform is being built for there is no
altitude to recover a stall-spin in. The planning-time fix is the turn-geometry
constraint (SPRAY-FLIGHT-SAFETY.md #4); until that lands this monitor is what
makes the problem visible.

Recovery is proven by RAISING the threshold above the observed roll rather than
by waiting for level flight — the aircraft loiters continuously after takeoff,
so "wait for it to stop turning" would just hang. Raising the limit and
watching the verdict flip is a direct test of the threshold logic against the
same live telemetry.
"""
import math
import time

import pytest

from tests.sitl import harness as h

pytestmark = [pytest.mark.sitl, pytest.mark.slow]

SPEEDUP = 5.0
DEFAULT_LIMIT_DEG = 45.0     # GuardianConfig.bank_warn_deg


def _bank(client) -> dict:
    return h.guardian(client).get("monitors", {}).get("bank", {})


def _raw_roll_range(client, secs: float) -> tuple[float, float]:
    """Min/max |roll| in degrees straight from telemetry over a window."""
    lo, hi = 360.0, 0.0
    deadline = time.monotonic() + secs
    while time.monotonic() < deadline:
        r = abs(math.degrees(h.telem(client).get("roll") or 0.0))
        lo, hi = min(lo, r), max(hi, r)
        time.sleep(0.25)
    return lo, hi


def test_a_real_turn_raises_the_bank_warning(client):
    h.launch(client, speedup=SPEEDUP, fresh_eeprom=True)
    h.set_failsafe(client, gcs_enable=0)
    h.takeoff(client, alt=80)
    h.wait_for(client, lambda t: t["armed"], 30, "vehicle armed")
    h.wait_for(client, lambda t: t["altitude"] > 60, 180, "climb-out above 60m")

    snap = h.wait_warning(client, "bank", 120,
                          "guardian names a steep bank during a real turn")
    mon = (snap.get("guardian") or {}).get("monitors", {}).get("bank", {})
    self_roll = mon.get("roll_deg", 0.0)
    assert self_roll >= DEFAULT_LIMIT_DEG, mon
    assert mon.get("ok") is False, mon
    assert mon.get("limit_deg") == DEFAULT_LIMIT_DEG, (
        "above bank_low_alt_m the limit must NOT be the tightened one")
    assert mon.get("low_alt") is False, mon
    # Cross-check the verdict against RAW attitude, so the monitor can't be
    # passing on something other than the roll the operator sees. Compared as
    # a range, not an instant: the guardian ticks at 1 Hz while the aircraft
    # rolls continuously, so two reads taken moments apart legitimately differ
    # by several degrees (54.2 vs 51.0 when this was first written).
    lo, hi = _raw_roll_range(client, 6.0)
    assert hi >= DEFAULT_LIMIT_DEG, (
        f"guardian reported {self_roll} deg but raw ATTITUDE never exceeded "
        f"{DEFAULT_LIMIT_DEG} (observed {lo:.1f}..{hi:.1f})")

    # Warn-only by default — a bank must not have commanded anything.
    assert (snap.get("guardian") or {}).get("rtl_source") is None, snap["guardian"]

    # Raise the limit past what the aircraft is actually doing: the same live
    # telemetry must now read as clear.
    r = client.post("/api/safety/guardian", json={"bank_warn_deg": 89.0})
    assert r.status_code == 200, r.text
    h.wait_for(client,
               lambda t: ((t.get("guardian") or {}).get("monitors", {})
                          .get("bank", {}).get("ok") is True),
               60, "bank monitor clears once the limit is above the real roll")

    events = h.recent_events(client, 400)
    assert h.has_event(events, "guardian", "config_changed")

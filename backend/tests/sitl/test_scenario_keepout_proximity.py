"""Scenario: keepout-proximity — the flown position is checked against the
zones the plan avoided on paper (SPRAY-FLIGHT-SAFETY.md item 5).

`coverage.py` clips spray passes around keepouts and `reroute.py` routes
connecting legs around hazards, but both are planning-time. This proves the
second layer: a hazard ring placed on the aircraft's ACTUAL position is
detected from live GLOBAL_POSITION_INT, named, and cleared.

The ring is synthetic, which is the honest thing here rather than a shortcut —
these rings always come from outside (OSM via the planner, or the operator).
What is under test is the proximity math and the monitor chain against a real
position stream, not where the polygon came from.

Also pins the fail-safe that matters most: uploading a new mission CLEARS the
cached rings, so a new field can never be flown against the previous field's
zones — stale rings would read as a confident all-clear over unsurveyed ground.
"""
import pytest

from tests.sitl import harness as h

pytestmark = [pytest.mark.sitl, pytest.mark.slow]

SPEEDUP = 5.0
BUFFER_M = 60.0     # generous: the aircraft is moving, ticks are 1 Hz wall


def _keepout(client) -> dict:
    return h.guardian(client).get("monitors", {}).get("keepout", {})


def _ring_around(lat: float, lon: float, half_m: float = 40.0) -> list[dict]:
    """Closed square ring centred on a point."""
    pts = []
    for dn, de in ((-half_m, -half_m), (-half_m, half_m),
                   (half_m, half_m), (half_m, -half_m)):
        la, lo = h.offset(lat, lon, dn, de)
        pts.append({"lat": la, "lon": lo})
    pts.append(dict(pts[0]))
    return pts


def test_live_position_is_checked_against_hazard_rings(client):
    h.launch(client, speedup=SPEEDUP, fresh_eeprom=True)
    h.set_failsafe(client, gcs_enable=0)
    h.takeoff(client, alt=80)
    h.wait_for(client, lambda t: t["armed"], 30, "vehicle armed")
    h.wait_for(client, lambda t: t["altitude"] > 60, 180, "climb-out above 60m")

    # With nothing loaded the monitor must report UNKNOWN — never a green tick.
    before = _keepout(client)
    assert before.get("known") is False, before
    assert before.get("hazard_dist_m") is None, before

    # Drop a powerline ring on where the aircraft actually is.
    here = h.telem(client)
    r = client.post("/api/safety/keepouts", json={
        "zones": {"powerline": [{"kind": "powerline",
                                 "coords": _ring_around(here["lat"], here["lon"])}],
                  "water": [{"kind": "water",
                             "coords": _ring_around(*h.offset(here["lat"],
                                                              here["lon"], 3000, 0))}]},
        "hazard_buffer_m": BUFFER_M})
    assert r.status_code == 200, r.text
    assert (r.json()["hazards"], r.json()["keepouts"]) == (1, 1)

    snap = h.wait_warning(client, "powerline", 120,
                          "guardian names the hazard the aircraft is near")
    mon = (snap.get("guardian") or {}).get("monitors", {}).get("keepout", {})
    assert mon.get("known") is True, mon
    assert mon.get("ok") is False, mon
    assert mon.get("hazard_kind") == "powerline", mon
    assert mon.get("hazard_dist_m") is not None, mon
    assert mon.get("hazard_dist_m") < BUFFER_M, mon
    # The distant pond is measured but must never be the thing that warns.
    assert "water" not in " ".join(
        (snap.get("guardian") or {}).get("warnings", [])).lower()
    # Warn-only by default: an RTL could steer ACROSS the very line it is near.
    assert (snap.get("guardian") or {}).get("rtl_source") is None, snap["guardian"]

    # A new mission must reset proximity to unknown rather than reuse the rings.
    home = h.telem(client)
    h.upload_mission(client, [
        {"command": "WAYPOINT", "lat": home["lat"], "lon": home["lon"], "alt": 80},
    ])
    h.wait_for(client,
               lambda t: ((t.get("guardian") or {}).get("monitors", {})
                          .get("keepout", {}).get("known") is False),
               30, "mission upload clears the previous field's rings")
    assert client.get("/api/safety/keepouts").json()["known"] is False

    events = h.recent_events(client, 400)
    assert h.has_event(events, "guardian", "keepouts_loaded")
    assert h.has_event(events, "guardian", "keepouts_cleared")

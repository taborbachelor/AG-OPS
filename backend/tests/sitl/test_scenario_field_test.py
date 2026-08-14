"""Scenario: field-test — the maiden-flight dress rehearsal.

A COMPLETE autonomous flight with zero stick input, exactly what the real
first flight must do: takeoff -> waypoints -> return -> auto-land -> disarm.
If this scenario is red, the aircraft does not fly. Runs at speedup 5 from a
fresh EEPROM (firmware defaults), so it also re-proves the force-arm + AUTO
path against a totally unconfigured vehicle.
"""
import pytest

from tests.sitl import harness as h

pytestmark = [pytest.mark.sitl, pytest.mark.slow]

SPEEDUP = 5.0


def test_full_autonomous_flight(client):
    ready = h.launch(client, speedup=SPEEDUP, fresh_eeprom=True)
    lat = ready.get("home_lat") or ready["lat"]
    lon = ready.get("home_lon") or ready["lon"]

    # A small square job: climb out, two legs, then RTL. Backend inserts home
    # as seq 0, so these are seqs 1..4.
    wp1 = h.offset(lat, lon, 500, 0)
    wp2 = h.offset(lat, lon, 500, 500)
    h.upload_mission(client, [
        {"command": "TAKEOFF", "lat": lat, "lon": lon, "alt": 40},
        {"command": "WAYPOINT", "lat": wp1[0], "lon": wp1[1], "alt": 60},
        {"command": "WAYPOINT", "lat": wp2[0], "lon": wp2[1], "alt": 60},
        {"command": "RTL", "lat": lat, "lon": lon, "alt": 0},
    ])

    h.start_mission(client)          # AUTO, ack-checked
    h.force_arm(client)              # fresh EEPROM => pre-arm needs force

    h.wait_for(client, lambda t: t["armed"], 30, "vehicle armed")
    h.wait_for(client, lambda t: t["altitude"] > 30, 120,
               "climb-out above 30m (autonomous takeoff)")
    h.wait_for(client, lambda t: t["mission_seq"] >= 2, 180,
               "first waypoint leg active")
    h.wait_for(client, lambda t: t["mission_seq"] >= 4, 300,
               "RTL mission item reached")
    h.wait_for(client, lambda t: h.dist_home_m(t) < 300, 300,
               "returned to within 300m of home")

    # Autonomous landing: approach + NAV_LAND at home; ArduPlane auto-disarms
    # on touchdown — the flight ends with zero human input.
    r = client.post("/api/vehicle/land")
    assert r.status_code == 200, f"land failed: {r.text}"
    final = h.wait_for(client, lambda t: not t["armed"], 420,
                       "auto-land touchdown + self-disarm")
    assert final["altitude"] < 15, f"disarmed but not on the ground: {final['altitude']}m"

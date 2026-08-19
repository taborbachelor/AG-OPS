"""Scenario: onboard exclusion fence — hazards enforced with no GCS.

Proves the thing that made keepouts a laptop-only concept until now: the
planner's hazard rings actually reach the flight controller, over
MISSION_TYPE_FENCE, and are held there.

The second assertion is the one that matters most. `mission_type` is a
MAVLink 2 message extension; on the v10 bindings this project used until
2026-08-19 the field does not exist, every mission message is implicitly
type 0 (MISSION), and this exact upload would have been accepted by the
vehicle as a REGULAR MISSION — silently replacing the flight plan with a list
of fence vertices while reporting success at every step. So the test uploads a
real mission first and re-reads it afterwards. If the dialect ever regresses,
this goes red here rather than in a field with a loaded aircraft.
"""
import pytest

from tests.sitl import harness as h

pytestmark = [pytest.mark.sitl, pytest.mark.slow]

SPEEDUP = 5.0

# Far enough from home that the 30 m clearance check passes; close enough to be
# a plausible hazard for the field being flown.
HAZARD_OFFSET_M = 500.0
HAZARD_SIZE_M = 120.0


def _hazard_ring(lat, lon):
    """A closed square 'powerline corridor' NE of home, in gis_zones' shape."""
    c_lat, c_lon = h.offset(lat, lon, HAZARD_OFFSET_M, HAZARD_OFFSET_M)
    half = HAZARD_SIZE_M / 2.0
    corners = [(-half, -half), (-half, half), (half, half), (half, -half)]
    coords = []
    for n, e in corners:
        la, lo = h.offset(c_lat, c_lon, n, e)
        coords.append({"lat": la, "lon": lo})
    coords.append(coords[0])              # gis_zones rings are closed
    return {"kind": "powerline", "coords": coords}


def test_hazard_ring_reaches_the_flight_controller(client):
    h.launch(client, speedup=SPEEDUP, fresh_eeprom=True)
    t = h.wait_flight_ready(client)
    home_lat, home_lon = t["home_lat"], t["home_lon"]

    # A real mission, so we can prove the fence transfer does not eat it.
    wp1 = h.offset(home_lat, home_lon, 200, 0)
    wp2 = h.offset(home_lat, home_lon, 200, 200)
    mission = [
        {"command": "TAKEOFF", "lat": home_lat, "lon": home_lon, "alt": 50},
        {"command": "WAYPOINT", "lat": wp1[0], "lon": wp1[1], "alt": 50},
        {"command": "WAYPOINT", "lat": wp2[0], "lon": wp2[1], "alt": 50},
    ]
    assert h.upload_mission(client, mission)["ok"], "mission upload failed"
    before = client.get("/api/mission/download").json()["items"]
    assert len(before) == 3, before

    # Arm the monitor — the same call pushes the hard fence (push_to_vehicle
    # defaults on), which is the seam that the 86c6a6e bug taught us to keep
    # in one place.
    r = client.post("/api/safety/keepouts", json={
        "zones": [_hazard_ring(home_lat, home_lon)], "hazard_buffer_m": 20})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hazards"] == 1, body

    fence = body["fence"]
    assert fence["attempted"] is True, fence
    assert fence.get("ok") is True, f"fence upload failed: {fence}"
    assert fence["polygons"] == 1, fence
    assert fence["points"] == 4, fence          # square, closing vertex dropped

    # Read it back off the vehicle: a send is not proof, an echo is (M1b).
    held = client.get("/api/safety/exclusions").json()
    assert held["points"] == 4, held
    assert held["supported"] is True, held
    assert all(i["vertex_count"] == 4 for i in held["items"]), held
    assert all(i["command"] == 5002 for i in held["items"]), held

    # THE REGRESSION GUARD: the flight plan must be untouched.
    after = client.get("/api/mission/download").json()["items"]
    assert len(after) == 3, (
        "fence transfer clobbered the mission — mission_type is not reaching "
        f"the vehicle. Got {len(after)} items, expected 3: {after}")
    for a, b in zip(before, after):
        assert a["command"] == b["command"], (a, b)
        assert abs(a["lat"] - b["lat"]) < 1e-6, (a, b)
        assert abs(a["lon"] - b["lon"]) < 1e-6, (a, b)

    # And clearing is real, not just local state.
    r = client.delete("/api/safety/keepouts?clear_fence=true")
    assert r.status_code == 200, r.text
    assert r.json()["fence_cleared"] is True, r.json()
    assert client.get("/api/safety/exclusions").json()["points"] == 0

    events = h.recent_events(client, 300)
    assert h.has_event(events, "fence", "upload")

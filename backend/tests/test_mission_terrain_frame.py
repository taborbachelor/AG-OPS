"""Terrain-framed mission items: the frame on the wire, and the guard in front.

Seam S3 said a spray-plan `alt` means metres AGL. Until now every mission item
went up as `MAV_FRAME_GLOBAL_RELATIVE_ALT`, which is height above HOME — the
same number only while the ground stays level with the launch point. These tests
cover closing that: the frame reaching the vehicle, surviving a round trip, and
being refused when it would be a lie.

The refusals are the point. A terrain-framed mission is a promise that something
knows the ground height along the whole route, so uploading one over unbundled
ground, or to a vehicle with TERRAIN_ENABLE=0, has to fail on the bench with the
reason named. Quietly falling back to above-home framing would leave a plan that
still says "20 m" while meaning something else entirely.
"""

import pytest
from fastapi.testclient import TestClient
from pymavlink import mavutil

from app.main import app
from app.routers import mission as mission_router
from app.vehicle_manager import _FRAME_TO_MAV, _MAV_TO_FRAME

HOME_LAT, HOME_LON = 39.9042, -95.7997
FAR_LAT, FAR_LON = 39.7392, -104.9903          # Denver — outside the bundle


@pytest.fixture
def client():
    return TestClient(app)


def _item(lat=HOME_LAT, lon=HOME_LON, frame="terrain", command="WAYPOINT"):
    return {"command": command, "lat": lat, "lon": lon, "alt": 20.0, "frame": frame}


def body_terrain():
    """One terrain-framed waypoint over the home field."""
    return {"items": [_item()]}


class FakeVM:
    """Stands in for the vehicle: records what upload_mission was handed."""

    def __init__(self, connected=True, terrain_enable=1.0):
        self.connected = connected
        self._terrain_enable = terrain_enable
        self.uploaded = None
        self.cleared = None

    def cached_value(self, name):
        return self._terrain_enable if name == "TERRAIN_ENABLE" else None

    def get_param(self, name):
        return self.cached_value(name)

    def upload_mission(self, items):
        self.uploaded = items
        return {"ok": True, "count": len(items),
                "frames": sorted({i.get("frame") or "relative" for i in items})}

    def clear_mission_keepouts(self, reason=None):
        self.cleared = reason

    def snapshot(self):
        return {}

    def request_terrain_report(self, lat, lon):
        return True


@pytest.fixture
def fake_vm(monkeypatch):
    vm = FakeVM()
    monkeypatch.setattr(mission_router, "vehicle_manager", vm)
    return vm


# --------------------------------------------------------------------------
# The frame itself
# --------------------------------------------------------------------------

def test_frame_constants_are_the_mavlink_ones():
    """Frame 10 is GLOBAL_TERRAIN_ALT; 3 is GLOBAL_RELATIVE_ALT."""
    assert _FRAME_TO_MAV["terrain"] == mavutil.mavlink.MAV_FRAME_GLOBAL_TERRAIN_ALT == 10
    assert _FRAME_TO_MAV["relative"] == mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT == 3
    assert _MAV_TO_FRAME[10] == "terrain"


def test_default_frame_is_still_relative(client, fake_vm):
    """Existing callers that never heard of frames must be unaffected."""
    body = {"items": [{"command": "WAYPOINT", "lat": HOME_LAT, "lon": HOME_LON,
                       "alt": 20.0}]}
    assert client.post("/api/mission/upload", json=body).status_code == 200
    assert fake_vm.uploaded[0]["frame"] == "relative"


def test_terrain_frame_reaches_the_vehicle(client, fake_vm):
    body = {"items": [_item(), _item(lat=39.95, lon=-95.85)]}
    resp = client.post("/api/mission/upload", json=body)
    assert resp.status_code == 200
    assert [i["frame"] for i in fake_vm.uploaded] == ["terrain", "terrain"]
    assert resp.json()["frames"] == ["terrain"]


def test_takeoff_and_land_cannot_be_terrain_framed(client, fake_vm):
    """Takeoff climbs from the launch point; landing ends at the ground."""
    for command in ("TAKEOFF", "LAND", "RTL"):
        body = {"items": [_item(command=command)]}
        resp = client.post("/api/mission/upload", json=body)
        assert resp.status_code == 422, command
        assert "terrain frame" in resp.text
    assert fake_vm.uploaded is None, "nothing should have reached the vehicle"


def test_mixed_frames_are_allowed(client, fake_vm):
    """A real spray plan: relative takeoff, terrain-following passes."""
    body = {"items": [_item(command="TAKEOFF", frame="relative"),
                      _item(), _item(lat=39.91, lon=-95.81),
                      _item(command="RTL", frame="relative")]}
    resp = client.post("/api/mission/upload", json=body)
    assert resp.status_code == 200
    assert resp.json()["frames"] == ["relative", "terrain"]


# --------------------------------------------------------------------------
# The guard
# --------------------------------------------------------------------------

def test_terrain_mission_outside_coverage_is_refused(client, fake_vm):
    """The bundling decision, enforced at the point of upload."""
    body = {"items": [_item(), _item(lat=FAR_LAT, lon=FAR_LON)]}
    resp = client.post("/api/mission/upload", json=body)
    assert resp.status_code == 400
    assert "N39W105" in resp.json()["detail"]
    assert "make_terrain" in resp.json()["detail"], "must say how to fix it"
    assert fake_vm.uploaded is None, "must not reach the vehicle at all"


def test_a_relative_mission_outside_coverage_is_still_allowed(client, fake_vm):
    """Terrain coverage gates the terrain frame, not every flight anywhere.

    Above-home framing makes no claim about the ground, so it needs no tiles.
    Gating it too would ground the aircraft for a reason that doesn't apply.
    """
    body = {"items": [_item(lat=FAR_LAT, lon=FAR_LON, frame="relative")]}
    assert client.post("/api/mission/upload", json=body).status_code == 200
    assert fake_vm.uploaded is not None


def test_terrain_disabled_on_the_vehicle_is_refused(client, fake_vm):
    """TERRAIN_ENABLE=0 means the aircraft ignores terrain data entirely."""
    fake_vm._terrain_enable = 0.0
    resp = client.post("/api/mission/upload", json=body_terrain())
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "TERRAIN_ENABLE" in detail and "TERRAIN_SPACING=100" in detail
    assert fake_vm.uploaded is None


def test_unreadable_terrain_enable_is_refused_not_assumed(client, fake_vm):
    """Not knowing whether the vehicle honours the frame is not permission."""
    fake_vm._terrain_enable = None
    resp = client.post("/api/mission/upload", json=body_terrain())
    assert resp.status_code == 503
    assert "TERRAIN_ENABLE" in resp.json()["detail"]
    assert fake_vm.uploaded is None


def test_keepouts_are_still_cleared_after_a_terrain_upload(client, fake_vm):
    """The stale-rings guarantee must not have been lost to the new code path."""
    assert client.post("/api/mission/upload", json=body_terrain()).status_code == 200
    assert fake_vm.cleared == "mission_upload"


# --------------------------------------------------------------------------
# Readiness endpoint
# --------------------------------------------------------------------------

def test_terrain_readiness_separates_bundle_from_vehicle(client, fake_vm):
    """Us having a tile and the aircraft having loaded it are different facts."""
    resp = client.get("/api/mission/terrain",
                      params={"lat": HOME_LAT, "lon": HOME_LON})
    assert resp.status_code == 200
    body = resp.json()
    assert body["bundle"]["covers_point"] is True
    assert body["bundle"]["grid_spacing_m"] == 100
    assert "N39W096" in body["bundle"]["tiles"]
    assert body["vehicle"] is not None

    outside = client.get("/api/mission/terrain",
                         params={"lat": FAR_LAT, "lon": FAR_LON}).json()
    assert outside["bundle"]["covers_point"] is False

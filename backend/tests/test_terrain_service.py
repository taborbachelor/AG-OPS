"""Unit tests for serving TERRAIN_DATA and for the terrain-framed mission guard.

The protocol half is easy to get subtly, silently wrong: a gridbit decomposed on
the wrong axis, or a corner rounded the wrong way, still produces sixteen
plausible heights — just for the wrong patch of ground. So these tests check the
mapping against ArduPilot's own inverse (`grid_bitnum`) and against the stored
block, rather than against a hand-written expectation.

The guard half is the 2026-08-19 bundling decision made testable. A
terrain-framed mission over ground we have no tiles for must be refused on the
bench with the tile named, and a vehicle with TERRAIN_ENABLE=0 must be refused
too — because in both cases the aircraft would fly a plan whose altitude does
not mean what the plan says it means.

No link and no vehicle: `serve()` takes numbers and returns payloads.
"""

import pytest

from app import terrain_format as tf
from app import terrain_service as ts
from app.terrain_store import TerrainDataError, store

HOME_LAT, HOME_LON = 39.9042, -95.7997


@pytest.fixture(scope="module")
def tiles():
    return store()


@pytest.fixture
def service():
    return ts.TerrainService()


def _corner(lat=HOME_LAT, lon=HOME_LON, spacing=100):
    info = tf.locate(lat, lon, spacing)
    return info.block_lat_e7, info.block_lon_e7


# --------------------------------------------------------------------------
# The gridbit <-> block mapping
# --------------------------------------------------------------------------

def test_subgrid_origin_inverts_ardupilot_grid_bitnum():
    """Every bit must decompose back to the indices ArduPilot derived it from."""
    for gridbit in range(ts.GRIDBITS_PER_BLOCK):
        idx_x, idx_y = ts.subgrid_origin(gridbit)
        assert tf.grid_bitnum(idx_x, idx_y) == gridbit
        assert 0 <= idx_x <= tf.BLOCK_SIZE_X - tf.GRID_MAVLINK_SIZE
        assert 0 <= idx_y <= tf.BLOCK_SIZE_Y - tf.GRID_MAVLINK_SIZE


def test_a_full_mask_yields_every_grid_exactly_once(service):
    lat_e7, lon_e7 = _corner()
    out = service.serve(lat_e7, lon_e7, 100, (1 << 56) - 1)
    assert len(out) == 56
    assert sorted(p["gridbit"] for p in out) == list(range(56))
    assert all(len(p["data"]) == 16 for p in out)


def test_only_requested_bits_are_answered(service):
    lat_e7, lon_e7 = _corner()
    out = service.serve(lat_e7, lon_e7, 100, (1 << 0) | (1 << 17) | (1 << 55))
    assert [p["gridbit"] for p in out] == [0, 17, 55]
    assert service.stats["grids_sent"] == 3


def test_mask_bits_above_55_are_ignored(service):
    """A block only has 56 sub-grids; anything above is a malformed request."""
    lat_e7, lon_e7 = _corner()
    out = service.serve(lat_e7, lon_e7, 100, (1 << 63) | (1 << 56) | (1 << 2))
    assert [p["gridbit"] for p in out] == [2]


def test_the_requested_corner_is_echoed_verbatim(service):
    """ArduPilot matches on lat/lon within ~5.6 cm; echoing makes that exact.

    Answering with our own corner instead would risk every message being
    discarded and terrain never loading, with nothing on the wire to say so.
    """
    lat_e7, lon_e7 = _corner()
    for payload in service.serve(lat_e7, lon_e7, 100, 0xFF):
        assert payload["lat"] == lat_e7
        assert payload["lon"] == lon_e7
        assert payload["grid_spacing"] == 100


def test_served_heights_are_the_stored_block(service, tiles):
    """data[x*4+y] must be height[idx_x+x][idx_y+y] — ArduPilot's own unpacking."""
    lat_e7, lon_e7 = _corner()
    info = tf.locate(HOME_LAT, HOME_LON, 100)
    block = tiles.block_at(info.lat_degrees, info.lon_degrees,
                           info.grid_idx_x, info.grid_idx_y)

    for payload in service.serve(lat_e7, lon_e7, 100, (1 << 56) - 1):
        idx_x, idx_y = ts.subgrid_origin(payload["gridbit"])
        expected = []
        for x in range(4):
            expected.extend(block.height[idx_x + x][idx_y:idx_y + 4])
        assert payload["data"] == expected, "gridbit %u" % payload["gridbit"]


def test_heights_are_real_ground_not_zeros(service):
    """A block of zeros would look like a working system flying at sea level."""
    lat_e7, lon_e7 = _corner()
    heights = [h for p in service.serve(lat_e7, lon_e7, 100, (1 << 56) - 1)
               for h in p["data"]]
    assert len(heights) == 56 * 16
    assert all(250 < h < 500 for h in heights), "Sabetha ground is ~400 m AMSL"


def test_corner_rounding_survives_the_offset_truncation(service):
    """The corner the aircraft sends can land a hair below the exact grid line.

    Truncating there picks the neighbouring block and serves ground from 2.4 km
    away, which is why block_index_for_corner rounds. Nudge the corner by a few
    units of 1e-7 degrees in each direction; the answer must not move.
    """
    lat_e7, lon_e7 = _corner()
    baseline = service.serve(lat_e7, lon_e7, 100, 1)[0]["data"]
    for dlat, dlon in ((-3, 0), (3, 0), (0, -3), (0, 3), (-2, -2), (2, 2)):
        out = service.serve(lat_e7 + dlat, lon_e7 + dlon, 100, 1)
        assert out, "nudged corner refused (dlat=%d dlon=%d)" % (dlat, dlon)
        assert out[0]["data"] == baseline, "nudge moved us to another block"


def test_every_block_of_a_tile_is_servable(service, tiles):
    """Walk a real tile's blocks, corners included, and serve each one.

    Round-trips through the block's OWN stored corner, so this also proves
    block_index_for_corner inverts the corner ArduPilot would compute.
    """
    served = 0
    for grid_idx_x in (0, 1, 20, 46):
        for grid_idx_y in (0, 1, 15, 30):      # 30 is the last one inside the degree
            block = tiles.block_at(39, -96, grid_idx_x, grid_idx_y)
            out = service.serve(block.lat_e7, block.lon_e7, 100, 1)
            assert out, "block (%u,%u) refused" % (grid_idx_x, grid_idx_y)
            # gridbit 0 is the 4x4 window at the block's own corner
            expected = [block.height[x][y] for x in range(4) for y in range(4)]
            assert out[0]["data"] == expected, "block (%u,%u)" % (grid_idx_x, grid_idx_y)
            served += 1
    assert served == 16


def test_slack_blocks_belong_to_the_next_degree_tile(service, tiles):
    """A degree file is wider than its degree, and the overflow is not ours.

    `east_blocks` pads each row with two spare blocks, so N39W096 physically
    holds blocks whose south-west corner is already east of 95W. ArduPilot
    addresses those corners as N39W095 and would never ask for them as part of
    N39W096 — so we must not answer with them either, even though the bytes are
    sitting in a file we have. Serving them would be answering a request for one
    tile out of a different one.
    """
    stride = tf.east_blocks(39, -96, 100)
    assert stride == 33
    inside = tiles.block_at(39, -96, 0, 30)
    assert ts.block_index_for_corner(inside.lat_e7, inside.lon_e7, 100)[:2] == (39, -96)

    for slack_idx in (31, 32):
        block = tiles.block_at(39, -96, 0, slack_idx)
        lat_d, lon_d, _, _ = ts.block_index_for_corner(block.lat_e7, block.lon_e7, 100)
        assert (lat_d, lon_d) == (39, -95), "block %u should fall in the next tile" % slack_idx
        # N39W095 is not bundled, so this is a coverage refusal, not a silent answer
        assert service.serve(block.lat_e7, block.lon_e7, 100, 1) == []
        assert "N39W095" in service.last_refusal["detail"]


# --------------------------------------------------------------------------
# Refusals — sending nothing is a feature
# --------------------------------------------------------------------------

def test_spacing_mismatch_is_refused_not_relabelled(service):
    """Sending 100 m data labelled 30 m is a confident, wrong ground model."""
    lat_e7, lon_e7 = _corner()
    assert service.serve(lat_e7, lon_e7, 30, (1 << 56) - 1) == []
    assert service.stats["spacing_mismatch"] == 1
    detail = service.last_refusal["detail"]
    assert "TERRAIN_SPACING" in detail and "30" in detail


def test_outside_coverage_serves_nothing(service):
    """Denver: the aircraft must end up reporting terrain unhealthy, not flat."""
    lat_e7, lon_e7 = _corner(39.7392, -104.9903)
    assert service.serve(lat_e7, lon_e7, 100, (1 << 56) - 1) == []
    assert service.stats["no_coverage"] == 1
    assert "N39W105" in service.last_refusal["detail"]
    assert service.stats["grids_sent"] == 0


def test_a_broken_bundle_refuses_rather_than_raising(service, tmp_path):
    """The telemetry loop calls this; an exception there would kill the link."""
    def broken():
        raise TerrainDataError("no terrain tiles in %s" % tmp_path)

    svc = ts.TerrainService(store_factory=broken)
    lat_e7, lon_e7 = _corner()
    assert svc.serve(lat_e7, lon_e7, 100, 0xFF) == []
    assert svc.stats["data_error"] == 1
    assert svc.snapshot()["available"] is False


def test_snapshot_reports_coverage_and_counters(service):
    lat_e7, lon_e7 = _corner()
    service.serve(lat_e7, lon_e7, 100, 0b111)
    snap = service.snapshot()
    assert snap["available"] is True
    assert snap["grid_spacing_m"] == 100
    assert "N39W096" in snap["tiles"]
    assert snap["requests"] == 1 and snap["grids_sent"] == 3


def test_terrain_is_not_served_while_the_gcs_is_off_the_air():
    """A dead link is dead in BOTH directions.

    The `gcs_link` fault silences our heartbeat so the vehicle declares a GCS
    failsafe. If TERRAIN_DATA kept flowing over that same radio it would model a
    link that cannot exist -- and, worse, let a scenario claim the aircraft flew
    a terrain-following leg on its own cached tiles while the GCS was quietly
    feeding it. Measured live 2026-08-20: 280 grids were served AFTER the link
    was 'cut' before this was fixed.

    Asserted at the vehicle_manager seam rather than in TerrainService, because
    the service is deliberately unaware of the link -- the suppression belongs
    where the sending happens.
    """
    from app.vehicle_manager import VehicleManager

    sent = []

    class FakeMav:
        def terrain_data_send(self, *a, **k):
            sent.append(a)

    class FakeConn:
        target_system = 1
        target_component = 1
        mav = FakeMav()

    class FakeMsg:
        lat, lon, grid_spacing, mask = 399000000, -958000000, 100, 0b1

    vm = VehicleManager()
    vm.connection = FakeConn()

    served_calls = []
    vm._terrain.serve = lambda *a, **k: (served_calls.append(a) or [
        {"lat": a[0], "lon": a[1], "grid_spacing": 100, "gridbit": 0,
         "data": [0] * 16}])

    vm._serve_terrain_request(FakeMsg())
    assert len(sent) == 1, "terrain should be served on a healthy link"

    vm.set_gcs_heartbeat_suppressed(True)
    vm._serve_terrain_request(FakeMsg())
    assert len(sent) == 1, (
        "TERRAIN_DATA was transmitted while the GCS was off the air -- the "
        "aircraft is being fed over a link the test believes is dead")
    assert len(served_calls) == 1, (
        "the service was still consulted while suppressed; counters would move "
        "and a scenario could not tell served-nothing from served-something")

    vm.set_gcs_heartbeat_suppressed(False)
    vm._serve_terrain_request(FakeMsg())
    assert len(sent) == 2, "terrain must resume when the link comes back"

"""Unit tests for the bundled terrain tiles, the .DAT format, and the refusals.

Two things are being defended here.

The first is the format. The heights only mean anything if our block addressing
lands on the same bytes ArduPilot's would, so the constants are asserted against
ArduPilot's own values and the CRC against a published vector — a "cleanup" that
collapses the block overlap or shortens the CRC by one byte breaks terrain
following silently, and these tests are what make it break loudly instead.

The second is the fail-loud rule from the 2026-08-19 LANES decision. Bundling
tiles caps coverage, and the whole bargain is that leaving coverage is an error,
not a shrug. So most of what follows is deliberately about the bad cases: no
tile, empty tile, truncated tile, flipped byte, expired version, mixed spacing.
Each one used to be a plausible way to end up at 15 m AGL over ground the GCS
had quietly assumed was flat.

No network: the fetch tool is not exercised here, only the bytes it produced.
"""

import json
import shutil
import struct

import pytest

from app import terrain_format as tf
from app.terrain_store import (TerrainCoverageError, TerrainDataError,
                               TerrainStore, bundled_dir)

# Sabetha, KS — the same home as sim.SITL_HOME and the SITL harness.
HOME_LAT, HOME_LON = 39.9042, -95.7997


@pytest.fixture(scope="module")
def store():
    return TerrainStore()


# --------------------------------------------------------------------------
# The format itself
# --------------------------------------------------------------------------

def test_constants_match_ardupilot():
    """Transcribed from AP_Terrain.h. If these drift, every lookup is wrong."""
    assert (tf.BLOCK_SIZE_X, tf.BLOCK_SIZE_Y) == (28, 32)
    assert (tf.BLOCK_STEP_X, tf.BLOCK_STEP_Y) == (24, 28)
    assert tf.BLOCK_BYTES == 2048
    assert tf.BLOCK_STRUCT_BYTES == 1822
    assert tf.BLOCK_CRC_BYTES == 1821
    assert tf.FORMAT_VERSION == 1
    assert tf.VERSION_MINOR_MIN == 1


def test_blocks_overlap_by_one_mavlink_grid():
    """The overlap is why interpolation never needs a second block.

    size - step == 4 in both axes, so idx+1 is always inside the block.
    """
    assert tf.BLOCK_SIZE_X - tf.BLOCK_STEP_X == tf.GRID_MAVLINK_SIZE
    assert tf.BLOCK_SIZE_Y - tf.BLOCK_STEP_Y == tf.GRID_MAVLINK_SIZE


def test_crc_is_xmodem():
    """ArduPilot's crc16_ccitt is CRC-16/XMODEM; 0x31C3 is its check value."""
    assert tf.crc16_ccitt(b"123456789") == 0x31C3


def test_crc_covers_1821_bytes_with_crc_zeroed():
    """version_minor and the trailer are excluded, deliberately."""
    raw = bytearray(tf.BLOCK_BYTES)
    raw[:22] = struct.pack("<QiiHHH", tf.BITMAP_FULL, 399000000, -960000000,
                           0xBEEF, 1, 100)
    base = tf.block_crc(bytes(raw))

    # the crc field's own value must not feed the crc
    raw[16:18] = struct.pack("<H", 0x1234)
    assert tf.block_crc(bytes(raw)) == base

    # nor must version_minor, at offset 1821
    raw[tf.BLOCK_CRC_BYTES] = 7
    assert tf.block_crc(bytes(raw)) == base

    # but the last byte *inside* the covered range must
    raw[tf.BLOCK_CRC_BYTES - 1] ^= 0xFF
    assert tf.block_crc(bytes(raw)) != base


def test_tile_names_floor_toward_the_southwest():
    assert tf.tile_name(39, -96) == "N39W096"
    assert tf.tile_name(-36, 149) == "S36E149"
    assert tf.parse_tile_name("N39W096.DAT") == (39, -96)
    assert tf.parse_tile_name("S36E149") == (-36, 149)
    with pytest.raises(ValueError):
        tf.parse_tile_name("garbage")


def test_pack_unpack_round_trip():
    block = tf.Block(bitmap=tf.BITMAP_FULL, lat_e7=399000000, lon_e7=-960000000,
                     crc=0, version=tf.FORMAT_VERSION, spacing=100,
                     height=[[x * 100 + y for y in range(tf.BLOCK_SIZE_Y)]
                             for x in range(tf.BLOCK_SIZE_X)],
                     grid_idx_x=3, grid_idx_y=5, lon_degrees=-96, lat_degrees=39,
                     version_minor=tf.VERSION_MINOR_MIN)
    raw = tf.pack_block(block)
    assert len(raw) == tf.BLOCK_BYTES

    back = tf.unpack_block(raw)
    assert back.crc == tf.block_crc(raw)
    for field in ("bitmap", "lat_e7", "lon_e7", "version", "spacing",
                  "grid_idx_x", "grid_idx_y", "lon_degrees", "lat_degrees",
                  "version_minor"):
        assert getattr(back, field) == getattr(block, field), field
    assert back.height == block.height


# --------------------------------------------------------------------------
# The bundle on disk
# --------------------------------------------------------------------------

def test_bundle_loads_and_is_one_spacing(store):
    assert store.spacing == 100, "must match TERRAIN_SPACING on the vehicle"
    assert "N39W096" in store.tile_names()


def test_manifest_describes_the_files_actually_present(store):
    """A tile swapped or truncated after the fact is caught here, not in flight."""
    manifest = json.loads((bundled_dir() / "index.json").read_text(encoding="utf-8"))
    assert manifest["grid_spacing_m"] == store.spacing
    assert sorted(t["name"] for t in manifest["tiles"]) == store.tile_names()
    for entry in manifest["tiles"]:
        path = bundled_dir() / (entry["name"] + ".DAT")
        assert path.stat().st_size == entry["bytes"], entry["name"]


def test_home_field_has_a_plausible_height(store):
    """Sabetha sits around 400 m AMSL. A gross offset bug shows up here."""
    height = store.height_amsl(HOME_LAT, HOME_LON)
    assert 350.0 < height < 450.0
    assert store.height_agl(HOME_LAT, HOME_LON, height + 15.0) == pytest.approx(15.0)


def test_lookup_lands_on_the_stored_cell(store):
    """A query at a stored grid point must return that point's height.

    Sampled at cell centres and compared against the four surrounding stored
    heights: this is the test that would fail if our block addressing were off
    by a cell, a row, or a block.
    """
    tile = store._tiles[(39, -96)]
    checked = 0
    for blocknum in (0, 1, 33, 700, tile.blocks - 1):
        block = tile.block(blocknum)
        for gx, gy in ((0, 0), (5, 9), (tf.BLOCK_STEP_X - 1, tf.BLOCK_STEP_Y - 1)):
            lat_e7, lon_e7 = tf.add_offset(block.lat_e7, block.lon_e7,
                                           (gx + 0.5) * store.spacing,
                                           (gy + 0.5) * store.spacing)
            lat, lon = lat_e7 * 1e-7, lon_e7 * 1e-7
            if not store.covers(lat, lon):
                continue        # cell belongs to the neighbouring degree tile
            corners = [block.height[gx + i][gy + j] for i in (0, 1) for j in (0, 1)]
            got = store.height_amsl(lat, lon)
            assert min(corners) - 0.01 <= got <= max(corners) + 0.01
            checked += 1
    assert checked > 5


def test_interpolation_is_bilinear(store):
    """height_amsl must be the bilinear blend of the four cells locate() picks.

    Computed independently from the block's own stored heights, so this checks
    the real path — locate, block fetch, interpolate — rather than restating the
    formula. ArduPilot's AP_Terrain::height_amsl does exactly this blend, and a
    lookup that disagreed with the aircraft's own would be worse than useless.
    """
    for lat, lon in ((HOME_LAT, HOME_LON), (39.4137, -96.6021), (40.7773, -95.2311)):
        info = tf.locate(lat, lon, store.spacing)
        block = store._tiles[(info.lat_degrees, info.lon_degrees)].block(info.blocknum)

        h00 = block.height[info.idx_x][info.idx_y]
        h01 = block.height[info.idx_x][info.idx_y + 1]
        h10 = block.height[info.idx_x + 1][info.idx_y]
        h11 = block.height[info.idx_x + 1][info.idx_y + 1]
        avg1 = (1.0 - info.frac_x) * h00 + info.frac_x * h10
        avg2 = (1.0 - info.frac_x) * h01 + info.frac_x * h11
        expected = (1.0 - info.frac_y) * avg1 + info.frac_y * avg2

        assert store.height_amsl(lat, lon) == pytest.approx(expected)
        assert min(h00, h01, h10, h11) - 1e-6 <= expected <= max(h00, h01, h10, h11) + 1e-6


def test_block_cache_stays_bounded(store):
    """Planning across the whole bundle must not pull every block into memory.

    A block is ~900 Python ints; an unbounded cache over four tiles would be tens
    of MB for a GCS that only ever needs a handful of blocks at a time.
    """
    from app.terrain_store import BLOCK_CACHE_SIZE

    tile = store._tiles[(39, -96)]
    for blocknum in range(min(tile.blocks, BLOCK_CACHE_SIZE * 3)):
        tile.block(blocknum)
    assert len(tile._cache) <= BLOCK_CACHE_SIZE


def test_east_blocks_stride_varies_with_latitude(store):
    """Rows get shorter toward the poles; the real files prove the math."""
    assert tf.east_blocks(39, -96, 100) == 33
    assert tf.east_blocks(40, -96, 100) == 32
    for (lat_d, lon_d), tile in store._tiles.items():
        assert tile.blocks % tile.stride == 0, "%s is not a whole number of rows" % tile.path.name


# --------------------------------------------------------------------------
# Fail loud — the whole point of bundling
# --------------------------------------------------------------------------

def test_outside_coverage_raises_and_names_the_tile(store):
    """Denver. Not bundled, so this must be an error and not a height."""
    with pytest.raises(TerrainCoverageError) as exc:
        store.height_amsl(39.7392, -104.9903)
    assert "N39W105" in str(exc.value)
    assert exc.value.missing_tiles == ["N39W105"]
    assert "make_terrain" in str(exc.value), "must say how to fix it"


def test_no_silent_zero_outside_coverage(store):
    """The failure this whole module exists to prevent."""
    assert not store.covers(39.7392, -104.9903)
    with pytest.raises(TerrainCoverageError):
        store.height_agl(39.7392, -104.9903, 500.0)


def test_require_coverage_checks_a_whole_mission(store):
    inside = [(HOME_LAT, HOME_LON), (39.95, -95.85), (40.1, -96.4)]
    store.require_coverage(inside, "the mission")          # must not raise

    strays = inside + [(38.5, -95.8)]
    with pytest.raises(TerrainCoverageError) as exc:
        store.require_coverage(strays, "the mission")
    assert exc.value.missing_tiles == ["N38W096"]
    assert "the mission" in str(exc.value)


@pytest.fixture
def one_tile(tmp_path):
    """A private copy of a real tile, for tests that damage it."""
    dst = tmp_path / "N39W096.DAT"
    shutil.copy(bundled_dir() / "N39W096.DAT", dst)
    return tmp_path, dst


def test_zero_byte_tile_is_rejected(tmp_path):
    """The exact placeholder this repo shipped until 2026-08-19."""
    (tmp_path / "N39W096.DAT").write_bytes(b"")
    with pytest.raises(TerrainDataError) as exc:
        TerrainStore(tmp_path)
    assert "0 bytes" in str(exc.value)


def test_truncated_tile_is_rejected(one_tile):
    tmp_path, dst = one_tile
    with open(dst, "r+b") as fh:
        fh.truncate(tf.BLOCK_BYTES * 3 + 17)
    with pytest.raises(TerrainDataError) as exc:
        TerrainStore(tmp_path)
    assert "truncated" in str(exc.value)


def test_corrupt_block_is_rejected_on_read(one_tile):
    """A flipped byte deep in the file: caught by CRC when that block is read."""
    tmp_path, dst = one_tile
    target = 40
    with open(dst, "r+b") as fh:
        fh.seek(target * tf.BLOCK_BYTES + 100)
        byte = fh.read(1)
        fh.seek(target * tf.BLOCK_BYTES + 100)
        fh.write(bytes([byte[0] ^ 0xFF]))

    store = TerrainStore(tmp_path)
    with pytest.raises(TerrainDataError) as exc:
        store._tiles[(39, -96)].block(target)
    assert "CRC" in str(exc.value)


def test_expired_version_minor_is_rejected(tmp_path):
    """ArduPilot will not arm on this, so the GCS must not fly on it either.

    Reproduces what ArduPilot's own create_terrain.py emits: version_minor 0.
    """
    src = (bundled_dir() / "N39W096.DAT").read_bytes()[:tf.BLOCK_BYTES]
    raw = bytearray(src)
    raw[tf.BLOCK_CRC_BYTES] = 0                       # outside the CRC, so it stays valid
    assert tf.block_crc(bytes(raw)) == tf.unpack_block(bytes(raw)).crc
    (tmp_path / "N39W096.DAT").write_bytes(bytes(raw))

    with pytest.raises(TerrainDataError) as exc:
        TerrainStore(tmp_path)
    assert "arm" in str(exc.value)


def test_mixed_spacing_bundle_is_rejected(tmp_path):
    """One TERRAIN_SPACING per bundle; a mix means the vehicle is wrong somewhere."""
    shutil.copy(bundled_dir() / "N39W096.DAT", tmp_path / "N39W096.DAT")

    raw = bytearray((bundled_dir() / "N39W097.DAT").read_bytes()[:tf.BLOCK_BYTES])
    raw[20:22] = struct.pack("<H", 30)                # claim a 30 m grid
    raw[16:18] = struct.pack("<H", tf.block_crc(bytes(raw)))
    (tmp_path / "N39W097.DAT").write_bytes(bytes(raw))

    with pytest.raises(TerrainDataError) as exc:
        TerrainStore(tmp_path)
    assert "spacing" in str(exc.value).lower() or "grid" in str(exc.value)


def test_empty_and_missing_directories_are_rejected(tmp_path):
    with pytest.raises(TerrainDataError) as exc:
        TerrainStore(tmp_path)
    assert "empty" in str(exc.value)

    with pytest.raises(TerrainDataError) as exc:
        TerrainStore(tmp_path / "nope")
    assert "incomplete" in str(exc.value)

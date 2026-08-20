"""The ArduPilot terrain `.DAT` on-disk format, in one place.

Terrain data is BUNDLED with the aircraft rather than fetched (LANES decision,
2026-08-19). These are the bytes that bundle is made of, and both halves of the
system read them through this module: `terrain_store` answers height queries for
the GCS, and `tools/make_terrain.py` fetches and verifies the tiles. One
definition so the writer and the reader cannot drift apart.

Everything here is transcribed from ArduPilot's own source, not inferred from a
sample file — `libraries/AP_Terrain/AP_Terrain.h` (the `grid_block` struct),
`TerrainUtil.cpp` (`calculate_grid_info`, `get_block_crc`, `grid_bitnum`) and
`TerrainIO.cpp` (`east_blocks`, `seek_offset`, the read-side validation).

Three things about this format bite anyone who assumes:

1. **Blocks overlap.** A block holds 28x32 heights but steps 24x28, so
   neighbours share four rows/columns. That overlap is load-bearing: it is what
   lets any point's bilinear interpolation be satisfied from a single block, so
   never "tighten" the step to match the size.

2. **The CRC stops one byte short of the struct.** It covers 1821 of the 1822
   bytes, with the crc field itself zeroed; `version_minor` and everything after
   it are deliberately excluded so old firmware accepts newer blocks.

3. **`version_minor` must be >= 1 or the aircraft will not arm.** ArduPilot's
   `pre_arm_checks` fails with "terrain data expired, possible errors" when it
   reads a block below `TERRAIN_VERSION_MINOR_MIN`. Note that ArduPilot's own
   reference generator, `libraries/AP_Terrain/tools/create_terrain.py`, writes
   zero there — its `pack()` never emits the field, so it falls into the zero
   trailer. Tiles built with the stock script are refused by current firmware.
   Ours are checked for this on the way in (see `tools/make_terrain.py`).

Geodesy note: the lat/lon arithmetic below is deliberately ArduPilot's flat-earth
approximation with its float32 rounding, not a correct geodetic calculation. The
block corners we compute have to match the ones the flight controller computes
from the same degree reference, and it only tolerates a couple of centimetres of
disagreement (`TERRAIN_LATLON_EQUAL`). Being "more accurate" here means being
wrong.
"""

import math
import struct

# MAVLink ships terrain in 4x4 grids; a disk block is 7x8 of those.
GRID_MAVLINK_SIZE = 4
BLOCK_MUL_X = 7
BLOCK_MUL_Y = 8

# Heights per block: 28 north x 32 east.
BLOCK_SIZE_X = GRID_MAVLINK_SIZE * BLOCK_MUL_X   # 28, north
BLOCK_SIZE_Y = GRID_MAVLINK_SIZE * BLOCK_MUL_Y   # 32, east

# Step between block origins, in grid_spacing units — one MAVLink grid less
# than the size in each axis, which is where the overlap comes from.
BLOCK_STEP_X = (BLOCK_MUL_X - 1) * GRID_MAVLINK_SIZE   # 24, north
BLOCK_STEP_Y = (BLOCK_MUL_Y - 1) * GRID_MAVLINK_SIZE   # 28, east

# Blocks are padded to 2048 so SD-card IO stays block aligned. The struct itself
# is 1822 bytes; the CRC covers the 1821 before `version_minor`.
BLOCK_BYTES = 2048
BLOCK_STRUCT_BYTES = 1822
BLOCK_CRC_BYTES = 1821

FORMAT_VERSION = 1        # TERRAIN_GRID_FORMAT_VERSION
VERSION_MINOR_MIN = 1     # TERRAIN_VERSION_MINOR_MIN — below this, no arming

# Every 4x4 sub-grid present. Bundled tiles are always complete; a partial
# bitmap only happens when a block is being filled in from GCS messages.
BITMAP_FULL = (1 << 56) - 1

_HEADER = struct.Struct("<QiiHHH")   # bitmap, lat, lon, crc, version, spacing
_ROW = struct.Struct("<%uh" % BLOCK_SIZE_Y)
_FOOTER = struct.Struct("<HHhb")     # grid_idx_x, grid_idx_y, lon_degrees, lat_degrees


def _f32(v: float) -> float:
    """Round through single precision, the way the flight controller's math does."""
    return struct.unpack("f", struct.pack("f", v))[0]


# metres per 1e-7 degree of latitude, and its inverse (ArduPilot's Location.cpp)
SCALING_FACTOR = _f32(0.011131884502145034)
SCALING_FACTOR_INV = _f32(89.83204953368922)


def longitude_scale(lat_deg: float) -> float:
    """Longitude shrink factor at a latitude, floored the way ArduPilot floors it."""
    return max(_f32(math.cos(_f32(math.radians(lat_deg)))), 0.01)


def diff_longitude_e7(lon1_e7: int, lon2_e7: int) -> int:
    """lon1 - lon2 in 1e-7 degrees, taking the short way around the dateline."""
    if lon1_e7 * lon2_e7 >= 0:
        return lon1_e7 - lon2_e7
    dlon = lon1_e7 - lon2_e7
    if dlon > 1800000000:
        return dlon - 3600000000
    if dlon < -1800000000:
        return dlon + 3600000000
    return dlon


def distance_ne_e7(lat1_e7, lon1_e7, lat2_e7, lon2_e7):
    """(north, east) metres from point 1 to point 2. ArduPilot's get_distance_NE."""
    dlat = lat2_e7 - lat1_e7
    dlng = diff_longitude_e7(lon2_e7, lon1_e7) * longitude_scale(
        (lat1_e7 + lat2_e7) * 0.5 * 1.0e-7)
    return (dlat * SCALING_FACTOR, dlng * SCALING_FACTOR)


def add_offset(lat_e7, lon_e7, north_m, east_m):
    """Move a 1e-7 position by metres. ArduPilot's Location::offset."""
    dlat = int(float(north_m) * SCALING_FACTOR_INV)
    dlng = int((float(east_m) * SCALING_FACTOR_INV)
               / longitude_scale((lat_e7 + dlat * 0.5) * 1.0e-7))
    return (int(lat_e7 + dlat), int(lon_e7 + dlng))


def east_blocks(lat_degrees: int, lon_degrees: int, spacing: int) -> int:
    """Blocks per row in a degree file — the stride the block index is built on.

    ArduPilot measures a degree of longitude at the tile's *southern* edge and
    adds two blocks of slack, so a file is a little wider than the degree it is
    named for. Rows are therefore ragged in the geographic sense but uniform on
    disk, which is the whole point: `blocknum` is a flat index.
    """
    ref_lat = lat_degrees * 10 * 1000 * 1000
    ref_lon = lon_degrees * 10 * 1000 * 1000
    lat2, lon2 = add_offset(ref_lat, ref_lon + 10 * 1000 * 1000,
                            0, 2 * spacing * BLOCK_SIZE_Y)
    _, east_m = distance_ne_e7(ref_lat, ref_lon, lat2, lon2)
    return int(east_m / (spacing * BLOCK_STEP_Y))


def grid_bitnum(idx_x: int, idx_y: int) -> int:
    """Bit in a block's bitmap covering the 4x4 sub-grid holding (idx_x, idx_y)."""
    return (idx_y // GRID_MAVLINK_SIZE) + BLOCK_MUL_Y * (idx_x // GRID_MAVLINK_SIZE)


def tile_name(lat_degrees: int, lon_degrees: int) -> str:
    """`N39W096` — hemisphere letters around the floored degree corner."""
    return "%c%02u%c%03u" % ('S' if lat_degrees < 0 else 'N', min(abs(lat_degrees), 99),
                             'W' if lon_degrees < 0 else 'E', min(abs(lon_degrees), 999))


def parse_tile_name(name: str):
    """`N39W096` (with or without a .DAT suffix) -> (39, -96)."""
    stem = name[:-4] if name.upper().endswith(".DAT") else name
    if len(stem) != 7 or stem[0] not in "NS" or stem[3] not in "EW":
        raise ValueError("not a terrain tile name: %r" % name)
    lat = int(stem[1:3])
    lon = int(stem[4:7])
    return (-lat if stem[0] == 'S' else lat, -lon if stem[3] == 'W' else lon)


_CRC16_TAB = []
for _i in range(256):
    _c = _i << 8
    for _ in range(8):
        _c = ((_c << 1) ^ 0x1021) & 0xFFFF if _c & 0x8000 else (_c << 1) & 0xFFFF
    _CRC16_TAB.append(_c)


def crc16_ccitt(data: bytes, crc: int = 0) -> int:
    """ArduPilot's `crc16_ccitt` — CRC-16/XMODEM, poly 0x1021, no reflection."""
    for byte in data:
        crc = ((crc << 8) & 0xFFFF) ^ _CRC16_TAB[((crc >> 8) ^ byte) & 0xFF]
    return crc


def block_crc(raw: bytes) -> int:
    """CRC of a packed block: 1821 bytes with the crc field itself zeroed."""
    return crc16_ccitt(raw[:16] + b"\x00\x00" + raw[18:BLOCK_CRC_BYTES])


class GridInfo:
    """Where a position lands: which tile, which block, which cell, and the fractions.

    `idx_x`/`idx_y` are indices *within* the block and stay inside the step
    (0..23, 0..27), so `idx+1` is always in range for interpolation — that is the
    overlap earning its keep.
    """

    __slots__ = ("lat_degrees", "lon_degrees", "grid_idx_x", "grid_idx_y",
                 "idx_x", "idx_y", "frac_x", "frac_y", "block_lat_e7",
                 "block_lon_e7", "blocknum")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

    @property
    def tile(self) -> str:
        return tile_name(self.lat_degrees, self.lon_degrees)


def locate(lat: float, lon: float, spacing: int) -> GridInfo:
    """Break a lat/lon down into tile, block and cell indices.

    Mirrors `AP_Terrain::calculate_grid_info` plus the `blocknum` from
    `seek_offset`, so a lookup here lands on the same bytes the flight controller
    would read from the same file.
    """
    lat_e7 = int(lat * 1.0e7)
    lon_e7 = int(lon * 1.0e7)
    lat_degrees = math.floor(lat_e7 / 1.0e7)
    lon_degrees = math.floor(lon_e7 / 1.0e7)

    ref_lat = lat_degrees * 10 * 1000 * 1000
    ref_lon = lon_degrees * 10 * 1000 * 1000
    north_m, east_m = distance_ne_e7(ref_lat, ref_lon, lat_e7, lon_e7)

    idx_x = int(north_m / spacing)
    idx_y = int(east_m / spacing)
    grid_idx_x = idx_x // BLOCK_STEP_X
    grid_idx_y = idx_y // BLOCK_STEP_Y

    block_lat, block_lon = add_offset(
        ref_lat, ref_lon,
        grid_idx_x * BLOCK_STEP_X * float(spacing),
        grid_idx_y * BLOCK_STEP_Y * float(spacing))

    return GridInfo(
        lat_degrees=lat_degrees, lon_degrees=lon_degrees,
        grid_idx_x=grid_idx_x, grid_idx_y=grid_idx_y,
        idx_x=idx_x % BLOCK_STEP_X, idx_y=idx_y % BLOCK_STEP_Y,
        frac_x=(north_m - idx_x * spacing) / spacing,
        frac_y=(east_m - idx_y * spacing) / spacing,
        block_lat_e7=block_lat, block_lon_e7=block_lon,
        blocknum=east_blocks(lat_degrees, lon_degrees, spacing) * grid_idx_x + grid_idx_y)


class Block:
    """One 2048-byte grid block, unpacked."""

    __slots__ = ("bitmap", "lat_e7", "lon_e7", "crc", "version", "spacing",
                 "height", "grid_idx_x", "grid_idx_y", "lon_degrees",
                 "lat_degrees", "version_minor")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

    def height_at(self, idx_x: int, idx_y: int) -> int:
        return self.height[idx_x][idx_y]


def unpack_block(raw: bytes) -> Block:
    """Decode a 2048-byte block. Raises ValueError on anything malformed."""
    if len(raw) != BLOCK_BYTES:
        raise ValueError("block is %u bytes, expected %u" % (len(raw), BLOCK_BYTES))

    bitmap, lat_e7, lon_e7, crc, version, spacing = _HEADER.unpack_from(raw, 0)
    rows = []
    off = _HEADER.size
    for _ in range(BLOCK_SIZE_X):
        rows.append(list(_ROW.unpack_from(raw, off)))
        off += _ROW.size
    grid_idx_x, grid_idx_y, lon_degrees, lat_degrees = _FOOTER.unpack_from(raw, off)
    version_minor = raw[BLOCK_CRC_BYTES]

    return Block(bitmap=bitmap, lat_e7=lat_e7, lon_e7=lon_e7, crc=crc,
                 version=version, spacing=spacing, height=rows,
                 grid_idx_x=grid_idx_x, grid_idx_y=grid_idx_y,
                 lon_degrees=lon_degrees, lat_degrees=lat_degrees,
                 version_minor=version_minor)


def pack_block(block: Block) -> bytes:
    """Encode a block, computing its CRC. Inverse of `unpack_block`."""
    body = _HEADER.pack(block.bitmap, block.lat_e7, block.lon_e7, 0,
                        block.version, block.spacing)
    for row in block.height:
        body += _ROW.pack(*row)
    body += _FOOTER.pack(block.grid_idx_x, block.grid_idx_y,
                         block.lon_degrees, block.lat_degrees)
    body += bytes([block.version_minor])
    raw = body + bytes(BLOCK_BYTES - len(body))

    crc = block_crc(raw)
    return raw[:16] + struct.pack("<H", crc) + raw[18:]

"""Answering the aircraft's TERRAIN_REQUEST from the bundled tiles.

ArduPilot does not read our `.DAT` files — it keeps its own copies on the SD
card and fills them from `TERRAIN_DATA` messages a GCS sends. So terrain
following only works if something on this end answers. This is that something.

The exchange, as ArduPilot implements it in `TerrainGCS.cpp`:

    FC  -> GCS   TERRAIN_REQUEST(lat, lon, grid_spacing, mask)
    GCS -> FC    TERRAIN_DATA(lat, lon, grid_spacing, gridbit, data[16])   x N

`lat`/`lon` are the **south-west corner of a 28x32 block**, not of the tile and
not of the vehicle, and `mask` has one bit per 4x4 sub-grid the aircraft still
needs — up to 56 of them. Our tiles are stored in exactly those blocks, so a
request maps to one block and each set bit slices a 4x4 window out of it.

Three details are load-bearing:

1. **We echo the requested lat/lon back verbatim.** ArduPilot matches incoming
   `TERRAIN_DATA` against its cache with `TERRAIN_LATLON_EQUAL`, a ~5.6 cm
   window. If we answered with our own corner and our float rounding differed
   from the aircraft's by more than that, it would discard every message and
   terrain would silently never load. Echoing makes the match exact by
   construction; we still check separately that we found the block it meant.

2. **A spacing mismatch is refused, never served.** If the vehicle's
   `TERRAIN_SPACING` is not the grid our tiles were built at, sending 100 m data
   labelled 30 m puts a confident, wrong ground model under the aircraft. Better
   for terrain to be visibly absent.

3. **Outside coverage we send nothing at all.** The aircraft then reports
   `pending`, fails its own pre-arm terrain check, and refuses to fly a terrain
   mission — which is the loud failure the bundling decision requires. The
   `require_coverage` check at mission upload is what should catch this first;
   this is the backstop, and it stays silent-on-the-wire rather than guessing.

Pure logic, no MAVLink and no vehicle: `serve()` takes numbers and returns the
payloads to send, so the protocol is testable without a link.
"""

import math
import threading

from . import terrain_format as tf
from .terrain_store import TerrainCoverageError, TerrainDataError, store

# ArduPilot ships terrain in 4x4 grids; a block holds 7x8 = 56 of them, so a
# mask bit above 55 is nonsense and is dropped rather than trusted.
GRIDBITS_PER_BLOCK = tf.BLOCK_MUL_X * tf.BLOCK_MUL_Y     # 56
_HEIGHTS_PER_GRID = tf.GRID_MAVLINK_SIZE ** 2            # 16


def subgrid_origin(gridbit: int):
    """Bit number -> (idx_x, idx_y) of the 4x4 sub-grid's corner in the block.

    Inverse of `AP_Terrain::grid_bitnum`, and it must stay that way: getting the
    two axes the wrong way round still produces plausible heights, just from the
    wrong part of the field.
    """
    return ((gridbit // tf.BLOCK_MUL_Y) * tf.GRID_MAVLINK_SIZE,
            (gridbit % tf.BLOCK_MUL_Y) * tf.GRID_MAVLINK_SIZE)


def block_index_for_corner(lat_e7: int, lon_e7: int, spacing: int):
    """Which block a requested south-west corner refers to.

    Rounds rather than truncates. The corner the aircraft sends was itself built
    by adding integer-metre offsets to a degree reference, so it can land a
    hair below the exact grid line; `int()` there would silently pick the
    neighbouring block and serve terrain shifted by 2.4 km.
    """
    lat_degrees = math.floor(lat_e7 / 1.0e7)
    lon_degrees = math.floor(lon_e7 / 1.0e7)
    ref_lat = lat_degrees * 10 * 1000 * 1000
    ref_lon = lon_degrees * 10 * 1000 * 1000

    north_m, east_m = tf.distance_ne_e7(ref_lat, ref_lon, lat_e7, lon_e7)
    grid_idx_x = int(round(north_m / (tf.BLOCK_STEP_X * spacing)))
    grid_idx_y = int(round(east_m / (tf.BLOCK_STEP_Y * spacing)))
    return lat_degrees, lon_degrees, grid_idx_x, grid_idx_y


class TerrainService:
    """Serves terrain blocks to the aircraft, and refuses rather than guesses."""

    def __init__(self, store_factory=store):
        self._store_factory = store_factory
        self._store = None
        self._lock = threading.Lock()
        self.stats = {
            "requests": 0,          # TERRAIN_REQUEST messages seen
            "grids_sent": 0,        # TERRAIN_DATA messages produced
            "no_coverage": 0,       # request outside the bundled tiles
            "spacing_mismatch": 0,  # vehicle TERRAIN_SPACING != our bundle
            "bad_request": 0,       # unusable mask/indices
            "data_error": 0,        # tiles missing or corrupt
        }
        self.last_refusal = None

    def _get_store(self):
        if self._store is None:
            self._store = self._store_factory()
        return self._store

    def _refuse(self, kind, message):
        self.stats[kind] += 1
        self.last_refusal = {"reason": kind, "detail": message}
        return []

    def serve(self, lat_e7: int, lon_e7: int, spacing: int, mask: int) -> list:
        """Payloads answering one TERRAIN_REQUEST; [] means deliberately nothing.

        Each entry is ready to hand to `terrain_data_send`: the requested corner
        echoed back, the grid spacing, one gridbit, and its 16 heights.
        """
        with self._lock:
            self.stats["requests"] += 1

            try:
                tiles = self._get_store()
            except TerrainDataError as exc:
                return self._refuse("data_error", str(exc))

            if spacing != tiles.spacing:
                return self._refuse(
                    "spacing_mismatch",
                    "vehicle asked for a %u m grid but the bundle is %u m — set "
                    "TERRAIN_SPACING=%u on the vehicle. Refusing to send %u m data "
                    "labelled %u m." % (spacing, tiles.spacing, tiles.spacing,
                                        tiles.spacing, spacing))

            lat_d, lon_d, grid_idx_x, grid_idx_y = block_index_for_corner(
                lat_e7, lon_e7, spacing)
            if grid_idx_x < 0 or grid_idx_y < 0:
                return self._refuse("bad_request",
                                    "negative block index from corner %d,%d"
                                    % (lat_e7, lon_e7))

            if not tiles.has_tile(lat_d, lon_d):
                return self._refuse(
                    "no_coverage",
                    "aircraft asked for %s at %.6f, %.6f — not in the bundle (%s). "
                    "Sending nothing; the aircraft will report terrain unhealthy "
                    "rather than fly on a guess."
                    % (tf.tile_name(lat_d, lon_d), lat_e7 * 1e-7, lon_e7 * 1e-7,
                       ", ".join(tiles.tile_names())))

            try:
                block = tiles.block_at(lat_d, lon_d, grid_idx_x, grid_idx_y)
            except TerrainCoverageError as exc:
                return self._refuse("no_coverage", str(exc))
            except TerrainDataError as exc:
                return self._refuse("data_error", str(exc))

            # Did rounding land on the block the aircraft meant? Blocks are
            # BLOCK_STEP apart (2.4 km north / 2.8 km east at 100 m), so half a
            # step is a wide, unambiguous bound on "same block" — it catches a
            # real addressing fault without tripping on the centimetres of
            # float disagreement the echo above already makes harmless.
            north_off, east_off = tf.distance_ne_e7(
                block.lat_e7, block.lon_e7, lat_e7, lon_e7)
            if (abs(north_off) > 0.5 * tf.BLOCK_STEP_X * spacing
                    or abs(east_off) > 0.5 * tf.BLOCK_STEP_Y * spacing):
                return self._refuse(
                    "bad_request",
                    "block (%u, %u) of %s sits %.0f m N / %.0f m E from the "
                    "requested corner — our addressing disagrees with the aircraft's"
                    % (grid_idx_x, grid_idx_y, tf.tile_name(lat_d, lon_d),
                       north_off, east_off))

            out = []
            for gridbit in range(GRIDBITS_PER_BLOCK):
                if not mask & (1 << gridbit):
                    continue
                idx_x, idx_y = subgrid_origin(gridbit)
                heights = []
                for x in range(tf.GRID_MAVLINK_SIZE):
                    row = block.height[idx_x + x]
                    heights.extend(row[idx_y:idx_y + tf.GRID_MAVLINK_SIZE])
                # ArduPilot reads data[x*4 + y] into height[idx_x+x][idx_y+y].
                out.append({"lat": lat_e7, "lon": lon_e7, "grid_spacing": spacing,
                            "gridbit": gridbit, "data": heights})

            self.stats["grids_sent"] += len(out)
            return out

    def snapshot(self) -> dict:
        """Counters plus the last refusal, for the telemetry surface."""
        with self._lock:
            snap = dict(self.stats)
            snap["last_refusal"] = self.last_refusal
            try:
                tiles = self._get_store()
                snap["tiles"] = tiles.tile_names()
                snap["grid_spacing_m"] = tiles.spacing
                snap["available"] = True
            except TerrainDataError as exc:
                snap["available"] = False
                snap["error"] = str(exc)
            return snap

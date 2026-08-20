"""Ground height from the bundled terrain tiles — or a loud refusal.

Terrain data ships with the aircraft; nothing here reaches the network (LANES
decision, 2026-08-19). That decision came with a condition, and this module is
where it is enforced: **outside bundled coverage we raise, never return a
guess.** A missing tile that degraded quietly to "no terrain awareness" is the
failure that flies a spray pass at 15 m AGL believing the ground is flat, and it
would look exactly like a normal flight right up until it wasn't.

So there is no `height_amsl(...) -> float | None` here, and no default of zero.
Every path out of a lookup is either a real height or `TerrainCoverageError`
naming the tile that is missing and the command that fetches it. Callers may
catch it and refuse to plan or arm; they may not paper over it.

The 0-byte `N39W096.DAT` this repo carried until 2026-08-19 is the shape of the
problem — a file with the right name and no data. `load()` rejects it, along with
truncated files, bad CRCs and mixed grid spacings, at startup rather than at
15 m AGL.

Read side only. Writing/fetching tiles is `tools/make_terrain.py`; the on-disk
format itself is `terrain_format`.
"""

import json
import math
import os
import sys
import threading
from collections import OrderedDict
from pathlib import Path

from . import terrain_format as tf


class TerrainCoverageError(Exception):
    """A position was requested that the bundled tiles do not cover."""

    def __init__(self, message, missing_tiles=()):
        super().__init__(message)
        self.missing_tiles = list(missing_tiles)


class TerrainDataError(Exception):
    """The bundled tiles are unusable — missing, truncated, corrupt or mixed."""


def bundled_dir() -> Path:
    """Where the tiles live, running from source or from the packaged exe."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "terrain"        # PyInstaller bundle
    return Path(__file__).resolve().parents[2] / "terrain"


# Decoded blocks are held per tile, most-recently-used first. A block is ~900
# Python ints, so caching a whole tile would cost tens of MB and four tiles would
# be worse than the GCS has any reason to spend — while a mission over one field
# touches only a handful of blocks. ArduPilot itself keeps 12.
BLOCK_CACHE_SIZE = 64


class _Tile:
    """One degree file, with its blocks read lazily and kept in a small LRU."""

    def __init__(self, path: Path, lat_degrees: int, lon_degrees: int, spacing: int):
        self.path = path
        self.lat_degrees = lat_degrees
        self.lon_degrees = lon_degrees
        self.spacing = spacing
        self.stride = tf.east_blocks(lat_degrees, lon_degrees, spacing)
        self.blocks = os.path.getsize(path) // tf.BLOCK_BYTES
        self._cache = OrderedDict()
        self._lock = threading.Lock()

    def block(self, blocknum: int) -> tf.Block:
        with self._lock:
            hit = self._cache.get(blocknum)
            if hit is not None:
                self._cache.move_to_end(blocknum)
                return hit
            if not 0 <= blocknum < self.blocks:
                raise TerrainCoverageError(
                    "%s has no block %u (file holds %u) — the position is past the "
                    "edge of the tile's data" % (self.path.name, blocknum, self.blocks))
            with open(self.path, "rb") as fh:
                fh.seek(blocknum * tf.BLOCK_BYTES)
                raw = fh.read(tf.BLOCK_BYTES)
            block = _decode(raw, self.path, blocknum, self.spacing)
            self._cache[blocknum] = block
            if len(self._cache) > BLOCK_CACHE_SIZE:
                self._cache.popitem(last=False)
            return block


def _decode(raw: bytes, path: Path, blocknum: int, spacing: int) -> tf.Block:
    """Decode one block, refusing anything the flight controller would refuse."""
    try:
        block = tf.unpack_block(raw)
    except ValueError as exc:
        raise TerrainDataError("%s block %u: %s" % (path.name, blocknum, exc))

    if block.version != tf.FORMAT_VERSION:
        raise TerrainDataError("%s block %u: format version %u, expected %u"
                               % (path.name, blocknum, block.version, tf.FORMAT_VERSION))
    if block.spacing != spacing:
        raise TerrainDataError("%s block %u: %u m grid in a %u m tile"
                               % (path.name, blocknum, block.spacing, spacing))
    if block.crc != tf.block_crc(raw):
        raise TerrainDataError("%s block %u: CRC mismatch — the tile is corrupt"
                               % (path.name, blocknum))
    if block.version_minor < tf.VERSION_MINOR_MIN:
        # ArduPilot fails its pre-arm check on this, so the GCS should not
        # quietly fly on data the aircraft would itself refuse.
        raise TerrainDataError(
            "%s block %u: version_minor %u is below %u — ArduPilot rejects this as "
            "expired terrain data and will not arm. Re-fetch the tile."
            % (path.name, blocknum, block.version_minor, tf.VERSION_MINOR_MIN))
    return block


class TerrainStore:
    """The bundled tile set, and the only way to ask it for a height."""

    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else bundled_dir()
        self._tiles = {}
        self.spacing = None
        self.manifest = None
        self._load()

    # -- loading -------------------------------------------------------

    def _load(self):
        if not self.directory.is_dir():
            raise TerrainDataError(
                "no terrain directory at %s — the bundle is incomplete. "
                "Build it with: py tools\\make_terrain.py" % self.directory)

        manifest_path = self.directory / "index.json"
        if manifest_path.is_file():
            self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        for path in sorted(self.directory.glob("*.DAT")):
            self._add(path)

        if not self._tiles:
            raise TerrainDataError(
                "no terrain tiles in %s — the bundle is empty. "
                "Build it with: py tools\\make_terrain.py" % self.directory)

    def _add(self, path: Path):
        try:
            lat_degrees, lon_degrees = tf.parse_tile_name(path.name)
        except ValueError as exc:
            raise TerrainDataError(str(exc))

        size = os.path.getsize(path)
        if size == 0:
            raise TerrainDataError(
                "%s is 0 bytes — a tile name with no data behind it. Delete it or "
                "fetch the real tile: py tools\\make_terrain.py --tile %s"
                % (path.name, path.stem))
        if size % tf.BLOCK_BYTES:
            raise TerrainDataError(
                "%s is %u bytes, not a whole number of %u-byte blocks — truncated "
                "download" % (path.name, size, tf.BLOCK_BYTES))

        with open(path, "rb") as fh:
            head = fh.read(tf.BLOCK_BYTES)
        # Block 0 carries the tile's grid spacing; read it, then validate the
        # block against it so a corrupt header is caught here and not in flight.
        try:
            spacing = tf.unpack_block(head).spacing
        except ValueError as exc:
            raise TerrainDataError("%s block 0: %s" % (path.name, exc))
        if spacing <= 0:
            raise TerrainDataError("%s block 0: grid spacing %u is not usable"
                                   % (path.name, spacing))
        _decode(head, path, 0, spacing)

        if self.spacing is None:
            self.spacing = spacing
        elif spacing != self.spacing:
            # A mixed bundle silently changes what TERRAIN_SPACING the aircraft
            # must be set to, tile by tile. Refuse rather than pick one.
            raise TerrainDataError(
                "%s is a %u m grid but the bundle is %u m — all tiles must share a "
                "spacing, and it must match the vehicle's TERRAIN_SPACING"
                % (path.name, spacing, self.spacing))

        self._tiles[(lat_degrees, lon_degrees)] = _Tile(path, lat_degrees,
                                                        lon_degrees, spacing)

    # -- coverage ------------------------------------------------------

    def tile_names(self):
        return sorted(tf.tile_name(la, lo) for la, lo in self._tiles)

    def covers(self, lat: float, lon: float) -> bool:
        return (math.floor(lat), math.floor(lon)) in self._tiles

    def has_tile(self, lat_degrees: int, lon_degrees: int) -> bool:
        return (lat_degrees, lon_degrees) in self._tiles

    def block_at(self, lat_degrees: int, lon_degrees: int,
                 grid_idx_x: int, grid_idx_y: int) -> tf.Block:
        """One raw block, addressed the way the aircraft addresses it.

        This is what serving `TERRAIN_DATA` needs — whole blocks rather than an
        interpolated height. Raises `TerrainCoverageError` if the tile is not
        bundled or the indices fall outside it.
        """
        tile = self._tiles.get((lat_degrees, lon_degrees))
        if tile is None:
            name = tf.tile_name(lat_degrees, lon_degrees)
            raise TerrainCoverageError(
                "no bundled terrain for tile %s (have: %s)"
                % (name, ", ".join(self.tile_names())), missing_tiles=[name])
        if grid_idx_x < 0 or grid_idx_y < 0 or grid_idx_y >= tile.stride:
            raise TerrainCoverageError(
                "block index (%d, %d) is outside %s, whose rows are %u blocks wide"
                % (grid_idx_x, grid_idx_y, tile.path.name, tile.stride))
        return tile.block(tile.stride * grid_idx_x + grid_idx_y)

    def missing_for(self, points) -> list:
        """Tile names needed by these (lat, lon) points that we do not have."""
        missing = []
        for lat, lon in points:
            key = (math.floor(lat), math.floor(lon))
            if key not in self._tiles:
                name = tf.tile_name(*key)
                if name not in missing:
                    missing.append(name)
        return sorted(missing)

    def require_coverage(self, points, what="this flight"):
        """Raise unless every point is inside bundled coverage.

        Call this before planning or arming. The whole value of bundling tiles is
        that coverage is knowable up front — so check it up front, once, loudly,
        instead of discovering it as a failed lookup mid-pass.
        """
        missing = self.missing_for(points)
        if missing:
            raise TerrainCoverageError(
                "%s leaves bundled terrain coverage. Missing tile%s: %s. "
                "Bundled: %s. Fetch before flying: py tools\\make_terrain.py %s"
                % (what, "" if len(missing) == 1 else "s", ", ".join(missing),
                   ", ".join(self.tile_names()),
                   " ".join("--tile " + m for m in missing)),
                missing_tiles=missing)

    # -- lookup --------------------------------------------------------

    def height_amsl(self, lat: float, lon: float) -> float:
        """Ground height in metres AMSL, bilinear across the grid.

        Raises `TerrainCoverageError` outside the bundle. There is deliberately
        no "return None and let the caller decide" — see the module docstring.
        """
        info = tf.locate(lat, lon, self.spacing)
        tile = self._tiles.get((info.lat_degrees, info.lon_degrees))
        if tile is None:
            raise TerrainCoverageError(
                "no bundled terrain for %.6f, %.6f — that is tile %s, which is not "
                "in the bundle (%s). Fetch it before flying there: "
                "py tools\\make_terrain.py --tile %s"
                % (lat, lon, info.tile, ", ".join(self.tile_names()), info.tile),
                missing_tiles=[info.tile])

        block = tile.block(info.blocknum)

        # The one-square overlap guarantees idx+1 is in range; if it ever is not,
        # the block addressing is wrong and a silent clamp would hide it.
        h00 = block.height[info.idx_x][info.idx_y]
        h01 = block.height[info.idx_x][info.idx_y + 1]
        h10 = block.height[info.idx_x + 1][info.idx_y]
        h11 = block.height[info.idx_x + 1][info.idx_y + 1]

        avg1 = (1.0 - info.frac_x) * h00 + info.frac_x * h10
        avg2 = (1.0 - info.frac_x) * h01 + info.frac_x * h11
        return (1.0 - info.frac_y) * avg1 + info.frac_y * avg2

    def height_agl(self, lat: float, lon: float, alt_amsl: float) -> float:
        """Height above ground for a position given its AMSL altitude."""
        return alt_amsl - self.height_amsl(lat, lon)


_default = None
_default_lock = threading.Lock()


def store() -> TerrainStore:
    """The process-wide store over the bundled tiles, loaded once."""
    global _default
    with _default_lock:
        if _default is None:
            _default = TerrainStore()
        return _default

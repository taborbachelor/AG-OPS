"""Build the bundled terrain tile set.

Terrain ships with the aircraft (LANES decision, 2026-08-19), so this runs in the
office with internet, never in the field. Adding a field that falls outside
coverage is one command; see `terrain/README.md`.

Tiles come from ArduPilot's own terrain service rather than being generated here:

    https://terrain.ardupilot.org/tilesdat3/<TILE>.DAT.gz     100 m grid
    https://terrain.ardupilot.org/tilesdat1/<TILE>.DAT.gz      30 m grid

That is the same data the service's own generator produces (verified: a tile
pulled from `tilesdat3/` is byte-identical to the one `POST /generate` returns
for the same point), which means we are not maintaining a second implementation
of a binary format the aircraft has to accept. What we do maintain is the
checking: every block of every downloaded tile is decoded and CRC-verified
against `terrain_format` before it is allowed into the bundle, so a truncated
download or a service change cannot land a bad tile in the repo.

One check earns particular mention. ArduPilot refuses to arm on a tile whose
`version_minor` is below 1 — its own reference generator, `create_terrain.py`,
writes 0 there — so `--verify` fails loudly on it rather than letting the
problem surface as a pre-arm error on a bench day.
"""

import argparse
import gzip
import hashlib
import json
import math
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app import terrain_format as tf   # noqa: E402

TERRAIN_DIR = REPO / "terrain"

# The two grids the service publishes. 100 m is ArduPilot's default
# TERRAIN_SPACING; 30 m needs the vehicle parameter changed to match.
SOURCES = {100: "https://terrain.ardupilot.org/tilesdat3/%s.DAT.gz",
           30:  "https://terrain.ardupilot.org/tilesdat1/%s.DAT.gz"}

# The operating area the bundle is built for. Sabetha, KS — the same home
# position as sim.SITL_HOME and the SITL harness.
HOME_LAT, HOME_LON = 39.9042, -95.7997
DEFAULT_RADIUS_KM = 50.0
DEFAULT_SPACING = 100


def tiles_for_area(lat, lon, radius_km):
    """Degree tiles touched by a radius around a point.

    A bounding box over the circle, which over-covers at the corners — the right
    way round to be wrong when the consequence of a gap is a refused flight.
    """
    dlat = radius_km / 111.32
    dlon = radius_km / (111.32 * math.cos(math.radians(lat)))
    names = set()
    for la in (lat - dlat, lat + dlat):
        for lo in (lon - dlon, lon + dlon):
            names.add(tf.tile_name(math.floor(la), math.floor(lo)))
    # fill the interior of the box, not just its four corners
    lat0, lat1 = math.floor(lat - dlat), math.floor(lat + dlat)
    lon0, lon1 = math.floor(lon - dlon), math.floor(lon + dlon)
    for la in range(lat0, lat1 + 1):
        for lo in range(lon0, lon1 + 1):
            names.add(tf.tile_name(la, lo))
    return sorted(names)


def verify_tile(path, expect_spacing=None, name=None):
    """Decode every block. Returns (blocks, spacing); raises ValueError if bad.

    This is the gate between the network and the repo, so it checks everything
    the flight controller checks and one thing more (`blocknum` round-tripping),
    which is what would catch a file whose block layout disagrees with the
    stride we compute for it.
    """
    name = name or path.name
    lat_d, lon_d = tf.parse_tile_name(name)
    size = path.stat().st_size
    if size == 0:
        raise ValueError("%s is empty" % name)
    if size % tf.BLOCK_BYTES:
        raise ValueError("%s is %u bytes, not whole %u-byte blocks"
                         % (name, size, tf.BLOCK_BYTES))

    nblocks = size // tf.BLOCK_BYTES
    spacing = None
    stride = None
    with open(path, "rb") as fh:
        for bn in range(nblocks):
            raw = fh.read(tf.BLOCK_BYTES)
            block = tf.unpack_block(raw)

            if spacing is None:
                spacing = block.spacing
                if expect_spacing and spacing != expect_spacing:
                    raise ValueError("%s is a %u m grid, expected %u m"
                                     % (name, spacing, expect_spacing))
                stride = tf.east_blocks(lat_d, lon_d, spacing)
            elif block.spacing != spacing:
                raise ValueError("%s block %u: spacing %u, tile is %u"
                                 % (name, bn, block.spacing, spacing))

            if block.version != tf.FORMAT_VERSION:
                raise ValueError("%s block %u: version %u, expected %u"
                                 % (name, bn, block.version, tf.FORMAT_VERSION))
            if block.version_minor < tf.VERSION_MINOR_MIN:
                raise ValueError(
                    "%s block %u: version_minor %u < %u — ArduPilot treats this as "
                    "expired terrain data and refuses to arm"
                    % (name, bn, block.version_minor, tf.VERSION_MINOR_MIN))
            if block.crc != tf.block_crc(raw):
                raise ValueError("%s block %u: CRC mismatch" % (name, bn))
            if block.bitmap != tf.BITMAP_FULL:
                raise ValueError("%s block %u: incomplete bitmap 0x%x"
                                 % (name, bn, block.bitmap))
            if (block.lat_degrees, block.lon_degrees) != (lat_d, lon_d):
                raise ValueError("%s block %u: claims degree %d,%d"
                                 % (name, bn, block.lat_degrees, block.lon_degrees))
            if stride * block.grid_idx_x + block.grid_idx_y != bn:
                raise ValueError(
                    "%s block %u: indices (%u,%u) with stride %u put it at block %u — "
                    "the file's layout disagrees with our addressing"
                    % (name, bn, block.grid_idx_x, block.grid_idx_y, stride,
                       stride * block.grid_idx_x + block.grid_idx_y))
    return nblocks, spacing


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(name, spacing, dest_dir, force=False):
    """Download one tile, verify it, and only then put it in place."""
    final = dest_dir / (name + ".DAT")
    if final.exists() and final.stat().st_size and not force:
        print("  %s already present, skipping (use --force to refetch)" % name)
        return False

    url = SOURCES[spacing] % name
    tmp = dest_dir / (name + ".DAT.part")
    print("  %s <- %s" % (name, url))
    try:
        with urllib.request.urlopen(url, timeout=180) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise SystemExit(
                "no terrain tile %s on the server (HTTP 404). The service covers "
                "+-84 degrees latitude; check the tile name." % name)
        raise SystemExit("fetching %s failed: %s" % (name, exc))

    tmp.write_bytes(gzip.decompress(payload))
    try:
        blocks, got = verify_tile(tmp, expect_spacing=spacing, name=name)
    except ValueError as exc:
        tmp.unlink()
        raise SystemExit("REFUSED %s: %s" % (name, exc))

    tmp.replace(final)
    print("     ok: %u blocks, %u m grid, %.1f MB"
          % (blocks, got, final.stat().st_size / 1e6))
    return True


def write_manifest(dest_dir, spacing, area):
    """Record what is bundled and why, next to the bytes it describes."""
    tiles = []
    for path in sorted(dest_dir.glob("*.DAT")):
        lat_d, lon_d = tf.parse_tile_name(path.name)
        blocks, tile_spacing = verify_tile(path, expect_spacing=spacing)
        tiles.append({
            "name": path.stem,
            "lat_degrees": lat_d,
            "lon_degrees": lon_d,
            "covers": {"lat": [lat_d, lat_d + 1], "lon": [lon_d, lon_d + 1]},
            "blocks": blocks,
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        })

    manifest = {
        "format": "ardupilot-terrain-dat",
        "format_version": tf.FORMAT_VERSION,
        "version_minor": tf.VERSION_MINOR_MIN,
        "grid_spacing_m": spacing,
        "vehicle_param": {"TERRAIN_ENABLE": 1, "TERRAIN_SPACING": spacing},
        "source": SOURCES[spacing] % "<TILE>",
        "built_for": area,
        "tiles": tiles,
        "note": ("Coverage is exactly the degree squares listed. Outside them the "
                 "GCS refuses to plan or fly rather than assuming flat ground — "
                 "see backend/app/terrain_store.py."),
    }
    (dest_dir / "index.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lat", type=float, default=HOME_LAT)
    ap.add_argument("--lon", type=float, default=HOME_LON)
    ap.add_argument("--radius-km", type=float, default=DEFAULT_RADIUS_KM)
    ap.add_argument("--tile", action="append", default=[], metavar="N39W096",
                    help="add a specific tile; repeatable. Skips the radius math.")
    ap.add_argument("--spacing", type=int, default=DEFAULT_SPACING, choices=sorted(SOURCES),
                    help="metres between grid points; must match TERRAIN_SPACING "
                         "on the vehicle (default 100, ArduPilot's default)")
    ap.add_argument("--dir", type=Path, default=TERRAIN_DIR)
    ap.add_argument("--force", action="store_true", help="refetch tiles already present")
    ap.add_argument("--verify", action="store_true",
                    help="check the existing bundle and exit; no network")
    args = ap.parse_args()

    args.dir.mkdir(parents=True, exist_ok=True)

    if args.verify:
        found = sorted(args.dir.glob("*.DAT"))
        if not found:
            raise SystemExit("no tiles in %s" % args.dir)
        total = 0
        for path in found:
            blocks, spacing = verify_tile(path)
            total += blocks
            print("  %-12s %5u blocks  %3u m  %.1f MB  OK"
                  % (path.stem, blocks, spacing, path.stat().st_size / 1e6))
        print("%u tiles, %u blocks, all CRCs good" % (len(found), total))
        return

    prior = {}
    manifest_path = args.dir / "index.json"
    if manifest_path.is_file():
        prior = json.loads(manifest_path.read_text(encoding="utf-8")).get("built_for", {})

    if args.tile:
        # Adding a field outside coverage must not erase the record of the area
        # the bundle was originally built for.
        names = sorted(set(args.tile))
        area = dict(prior)
        area["added_tiles"] = sorted(set(area.get("added_tiles", [])) | set(names))
    else:
        names = tiles_for_area(args.lat, args.lon, args.radius_km)
        area = {"centre": {"lat": args.lat, "lon": args.lon},
                "radius_km": args.radius_km,
                "name": "Sabetha, KS" if (args.lat, args.lon) == (HOME_LAT, HOME_LON) else None}

    print("Bundling %u tile(s) at %u m: %s" % (len(names), args.spacing, ", ".join(names)))
    for name in names:
        fetch(name, args.spacing, args.dir, force=args.force)

    # Existing tiles are re-verified here too, so the manifest never describes a
    # tile nobody has checked.
    manifest = write_manifest(args.dir, args.spacing, area)
    total = sum(t["bytes"] for t in manifest["tiles"])
    print("\n%u tiles, %.1f MB total, %u m grid -> set TERRAIN_SPACING=%u on the vehicle"
          % (len(manifest["tiles"]), total / 1e6, args.spacing, args.spacing))


if __name__ == "__main__":
    main()

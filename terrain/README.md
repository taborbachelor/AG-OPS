# Bundled terrain

Ground-height tiles for the operating area, in ArduPilot's `.DAT` format. They
ship with the aircraft — nothing here is fetched at flight time.

**Why bundled:** the radio link is supervisory, not a control link. A ground
station that needs the internet to know how high the ground is has re-introduced
exactly the dependency the airframe design refuses. Tabor's call, 2026-08-19; see
the LANES decisions log.

**The price of that, and it is not optional:** coverage stops at the tiles in
this folder, so leaving coverage has to be a *refusal*, not a shrug. That is
enforced in `backend/app/terrain_store.py`, which raises `TerrainCoverageError`
rather than ever returning a height it does not have. A missing tile degrading
silently to no-terrain-awareness is the failure that flies a spray pass at 15 m
AGL believing the ground is flat.

## What is here

| | |
|---|---|
| Tiles | `N39W096` `N39W097` `N40W096` `N40W097` |
| Covers | 39–41 °N, 95–97 °W — a 50 km radius around Sabetha, KS (39.9042, −95.7997) |
| Grid | 100 m, from SRTM3 |
| Size | 12.5 MB, 6,110 blocks |
| Manifest | `index.json` — tile list, byte counts, SHA-256 |

`index.json` is checked against the files on disk by
`backend/tests/test_terrain_store.py`, so a tile that is swapped or truncated
after the fact fails the unit suite rather than a flight.

## Adding a tile when a field falls outside coverage

Two commands, run in the office — this needs internet, the field does not.

```
py tools\make_terrain.py --tile N38W095      # one specific tile
py tools\make_terrain.py --lat 38.9 --lon -94.7 --radius-km 50
```

Either form re-verifies every tile present and rewrites `index.json`. Then commit
the new `.DAT` and the manifest together.

To find out which tile you need, the name is the **floor** of the corner:
39.4, −95.8 → `N39W096`. Or just try to plan the mission — the coverage error
names the missing tile and prints the command that fetches it.

Check the bundle at any time, offline:

```
py tools\make_terrain.py --verify
```

That decodes every block of every tile and checks its CRC.

## On the vehicle

```
TERRAIN_ENABLE  1
TERRAIN_SPACING 100     <- must match the grid above
```

The whole folder can also be copied to the flight controller's SD card as
`APM/terrain/`, which gives the aircraft terrain data with no GCS attached at
all. That is the belt-and-braces position and it costs nothing but SD space.

`TERRAIN_SPACING` is per-bundle, not per-tile: the store refuses to load a mixed
set, because a mix would silently mean the parameter is wrong for some of your
tiles.

## Where the tiles come from

ArduPilot's own terrain service, one plain GET per tile:

```
https://terrain.ardupilot.org/tilesdat3/<TILE>.DAT.gz    100 m grid (SRTM3)
https://terrain.ardupilot.org/tilesdat1/<TILE>.DAT.gz     30 m grid (SRTM1)
```

These are byte-identical to what the service's own generator returns for the same
point, so we are not maintaining a second implementation of a binary format the
aircraft has to accept. Coverage is ±84° latitude; ocean tiles exist, so a
coastal field will not 404.

We still check everything on the way in — `tools/make_terrain.py` decodes every
block and verifies its CRC before a tile is allowed into the repo, and refuses
the file otherwise. Two findings from building this are worth keeping:

- **`version_minor` must be ≥ 1.** ArduPilot's `pre_arm_checks` *fails* on
  anything lower with "terrain data expired, possible errors" — it will not arm.
  ArduPilot's own reference generator, `libraries/AP_Terrain/tools/create_terrain.py`,
  writes 0 there (its `pack()` never emits the field). Tiles built with the stock
  script are refused by current firmware. The service's tiles are correct; we
  check anyway.
- **Blocks overlap.** A block holds 28×32 heights but steps 24×28, so neighbours
  share four rows and columns. That overlap is what lets any point's
  interpolation be satisfied from a single block. Anyone "tidying" the step to
  match the size breaks every lookup near a block edge.

## Why 100 m and not 30 m

Measured, not assumed. Against the same service's 30 m tiles over 20,000 points
in the operating area, the 100 m grid differs by a median of **0.7 m**, p90
**2.1 m**, p99 **4.9 m**. Going to 30 m costs **10.5×** the size — 133 MB instead
of 12.5 MB for the same four tiles, in the repo and in the installer, forever.

More to the point, 30 m spacing does not buy accuracy. SRTM's own vertical error
is roughly ±6 m relative and ±16 m absolute, so a finer grid resolves more
*detail* on a surface that is already uncertain by more than the difference
between the two grids. 100 m is also ArduPilot's default `TERRAIN_SPACING`.

**The consequence to carry forward:** at a 10–25 m spray altitude the terrain
model's uncertainty is a meaningful fraction of the clearance. Bundled terrain is
a *planning* input and a gross-error check. It is not a substitute for measuring
AGL, and terrain following alone should not be trusted to hold 15 m over
unsurveyed ground.

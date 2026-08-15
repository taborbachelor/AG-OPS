# Powerline keepouts — design spec (not implemented)

## Status (2026-08-15)
Not started, but **unblocked** — written 2026-08-14 from a read-only pass over the backend while
another session actively owned the repo mid-refactor. That work landed on `main` as `7bb3f60`
("Backend hardening") and touched exactly the files this doc targets: `gis_zones.py` gained
waterway-corridor/multipolygon-stitching fixes, `coverage.py`/`coverage_multi.py` and both
coverage routers gained the fail-closed zone-service handling and keepout-overflight tracking.
**The design here still holds** — the corridor-ring pattern (`_corridor_ring`, the linear-tag
branch in `_parse_overpass`) that this doc leans on is exactly what `gis_zones.py` extended for
waterways in that same hardening pass, so the "generalize the waterway branch to also cover
`power`" approach is, if anything, more clearly the right move now. Re-read the current file
contents before implementing (this doc no longer cites line numbers for that reason), but the
structural approach below is unchanged. See `SPRAY-FLIGHT-SAFETY.md` item 5 (live
keepout-proximity monitor) — that item reuses the same keepout data and geometry primitive this
feature needs, so the two pair naturally.

## Why this is next on the roadmap
Known gap already logged in this project's notes: "powerlines not in CDL — future: OSM
power=line buffer keepouts." USDA CDL is landcover classification (crop vs. water vs. forest); it
has no concept of infrastructure, so field auto-detect will never see a line running through a
field. This has to come from OSM, same as the existing water/trees/buildings keepouts.

Framing matters: water/trees/buildings keepouts exist to protect **spray quality** (don't
contaminate the pond, don't waste chemical on canopy). A powerline keepout exists to protect
**the airframe** — a low-altitude ag pass clipping a line is a crash, not a wasted pass. Treat
the default buffer and the UI treatment as a hazard, not a spray-efficiency nicety.

## Data source
OSM tags overhead power lines as **ways** with `power=line` (transmission) or `power=minor_line`
(distribution/farm feeders — this is the tag that actually matters for rural Kansas ag fields).
Explicitly exclude `location=underground` (no collision risk, would just create false keepouts)
and don't bother with `power=cable`/`power=pole`/`power=tower` (point/other features, not needed
for a corridor).

## Backend: reuse the waterway corridor pattern exactly
`gis_zones.py` already solved "linear OSM way → no-spray corridor" for waterways
(`_corridor_ring`, `_WATERWAY_RE`, the `"waterway" in tags and not closed` branch in
`_parse_overpass`). Power lines are the same shape of problem — generalize rather than duplicate:

1. **`_build_query`**: add one clause alongside the existing `waterway` one:
   ```python
   parts.append(f'way["power"~"^(line|minor_line)$"]{around};')
   ```
   Same single Overpass round-trip — no extra network cost, no cache-key change.

2. **`_classify`**: add
   ```python
   if tags.get("power") in ("line", "minor_line") and tags.get("location") != "underground":
       return "powerline"
   ```

3. **`_parse_overpass`**: the current check is
   `if "waterway" in tags and not (closed): ring = _corridor_ring(...)`.
   Generalize to a set: `_LINEAR_KEYS = {"waterway", "power"}`, then
   `if (_LINEAR_KEYS & tags.keys()) and not closed: ring = _corridor_ring(...)`.
   Everything else (`_corridor_ring`, the 2-point midpoint-insert special case, the closed-ring
   fallback) is reused unchanged.

4. **`fetch_zones` return shape**: `zones` dict grows a fourth key —
   `{"water": [...], "trees": [...], "buildings": [...], "powerline": [...]}`.

No new module, no new caching, no new Overpass query — this is additive to the existing function.
(Line numbers in earlier drafts of this doc were dropped since the other session's refactor will
have moved them — check current file contents before implementing, the structural approach above
still holds regardless of exact line placement.)

## Buffer default
Existing per-kind defaults: water 15m, trees 10m, buildings 10m — sized for spray drift /
physical obstruction. A powerline buffer needs to reflect flight clearance, which is a bigger
number. Propose **`powerline_buffer` default 20m**, and call out in the UI copy that this is a
lateral-clearance safety margin, not a spray-drift margin (matches the codebase's existing
philosophy comment in `routers/coverage.py`: *"over-standoff errs on the side of not spraying —
never the reverse"* — same logic, higher stakes here).

**Known limitation worth documenting, not fixing in v1:** `plan_coverage()` takes one global
`keepout_buffer_m` applied to *every* keepout ring, and both routers already pick `max()` across
whichever kinds are present in the query area. Adding a wider powerline buffer means that when a
line is anywhere in the fetch radius, water/trees/buildings keepouts *also* get buffered at the
powerline's (larger) distance for that request. This is over-conservative, not unsafe, and it's
consistent with the codebase's existing "largest buffer wins" choice — so ship v1 as-is. If it
turns out to bite (e.g. a big multi-field job loses real coverage because one distant field
corner has a line), the fix is per-ring buffers in `plan_coverage`'s keepout-clipping step — a
real (if contained) refactor, not this pass.

**Nice-to-have, explicitly deferred:** OSM often tags `voltage` on power ways. A v2 could scale
the buffer with voltage class (transmission lines want more clearance than a farm feeder), but
voltage tagging on rural distribution lines is inconsistent enough that a flat conservative
default is the right v1 call — same reasoning CDL/OSM data-quality caveats elsewhere in this
project already use.

## API surface changes
- `routers/coverage.py` → `AutoCoverageRequest`: add
  `powerline_buffer: float = Field(20.0, ge=0)` next to `water_buffer`/`tree_buffer`/
  `building_buffer`. In `plan_auto`, add `"powerline": req.powerline_buffer` to the `buffers`
  dict and `"powerline"` to the `for kind in (...)` loop — nothing else in that handler changes,
  the max-buffer/keepout-append logic is already kind-agnostic.
- `routers/coverage_multi.py` → `MultiRequest`: add the same field, and add it to the
  `max(req.water_buffer, req.tree_buffer, req.building_buffer, ...)` call and the
  `for kind in (...)` loop.
- `plan_coverage` / `plan_multi` themselves: **no changes** — they already treat "keepouts" as
  opaque ring lists with one scalar buffer.

## Frontend changes
- `MapView.jsx`: `ZONE_COLOR` gains `powerline: '<hazard color>'` — pick something visually
  distinct from the existing blue/green/orange/red set so it reads as "different kind of danger"
  (contamination vs. collision), e.g. a bold yellow/black hazard tone rather than another soft
  tint. Add `'powerline'` to the zone-kind render list (currently
  `['water', 'trees', 'buildings', 'holes']`).
- `MapView3D.jsx`: same zone-kind list exists for the Cesium view — mirror the addition. v1 can
  render it as the same flat ground corridor as the others; a real 3D wire strung between pole
  heights would read better but needs pole elevation data OSM doesn't reliably carry — punt to a
  later pass if wanted.
- `SprayPanel.jsx`: add a `bufPowerline` state (default 20) alongside `bufWater`/`bufTrees`/
  `bufBuildings`, a slider control, and add `powerline_buffer: bufPowerline` to the request body
  for both `plan_auto` and `plan_multi` calls.
- Legend: add a "Powerline" entry next to the existing water/trees/buildings/holes legend items.

## Operational caveat (put this in the UI, not just here)
OSM coverage of rural distribution lines is inconsistent — the project already hit this exact
failure mode for parcel boundaries near Sabetha ("OSM parcel coverage near Sabetha is sparse").
**Absence of a mapped line is not evidence of no line.** This is the one part of this feature
that actually matters for safety, so it shouldn't be a silent data-quality footnote: surface it
the same way `zones_unavailable` is already surfaced when Overpass is down — e.g. a persistent
"powerline keepouts are OSM-sourced and may be incomplete — confirm visually before flight" note
near the zones legend/toggle, always-on rather than only-on-failure, since here "the query
succeeded but under-counted" is the dangerous case, not "the query failed."

## Rollout order
1. Land after the current backend refactor session finishes and the repo is quiet — this touches
   `gis_zones.py`, both coverage routers, and two frontend components.
2. Backend first (`gis_zones.py` + both routers), unit-tested against a fake Overpass payload
   containing a `power=minor_line` way the same way the existing waterway tests already fake one.
3. Frontend after, once the API shape is confirmed live against SITL/real Overpass data near
   Sabetha (worth checking whether *any* lines are mapped there before trusting a demo on it —
   same sparse-OSM risk as the parcel-snap gotcha).
4. Pairs with `SPRAY-FLIGHT-SAFETY.md` item 5 (live keepout-proximity monitor) — same underlying
   keepout data, same `dist_to_zone_m` primitive.

"""Scenario: terrain following at spray altitude, with the link deliberately dead.

TASK-008 bundled the tiles, TASK-010 built the TERRAIN_REQUEST/TERRAIN_DATA
service — and delta said plainly that none of it had ever run against a real
ArduPilot: every test was a unit test over fake messages, so the protocol was
proven against ArduPilot's SOURCE but never against its BEHAVIOUR. This is that
run, and it is deliberately the hostile version of it: the GCS goes silent and
then issues no commands, so the aircraft holds height over ground using only
what it already has on board.

Two things are proven, and one is recorded as a hazard.

1. AGL is really held. The leg descends 25 m of real Kansas relief, so
   "held AGL" and "held above home" produce OPPOSITE traces and cannot be
   confused: height above ground stays put while height above home falls with
   the terrain. Both are asserted, because either one alone is satisfiable by
   the wrong behaviour.

2. Leaving bundled coverage fails loud, in both directions — the upload is
   refused with the missing tile named, and an aircraft asking for terrain we
   do not have is sent NOTHING rather than a guess. That is the cash-in of
   Tabor's 2026-08-19 bundling decision.

3. RECORDED HAZARD, not a failure of this feature: over falling ground, height
   above home stops meaning height above anything. Measured here at 4.5 m above
   home while genuinely 26.8 m above the ground — and `guardian.airborne_alt_m`
   is 5.0, so the guardian's airspeed and bank monitors gate themselves OFF
   mid-spray-pass. guardian.py's "KANSAS IS FLAT" note anticipated the
   inaccuracy; what it did not anticipate is that the monitors do not degrade,
   they switch off. See the assertions at the end of test 1.

WHY fresh_terrain=True IS LOAD-BEARING: ArduPilot persists terrain to its own
`cwd/terrain/` and reloads it on the next boot, and `fresh_eeprom` does not
clear it — a real 2.9 MB N39W096.DAT was sitting there when this was written.
Without clearing it this scenario would read a cache an EARLIER run filled and
pass with the TERRAIN_DATA service completely broken.
"""
import math
import time

import pytest

from tests.sitl import harness as h

pytestmark = [pytest.mark.sitl, pytest.mark.slow]

# Speedup 1: this scenario enables the GCS failsafe, which times heartbeats in
# SIM seconds while we send them in WALL seconds.
SPEEDUP = 1.0

SPRAY_AGL_M = 25.0          # inside the 10-25 m band real aerial application uses

# A monotonic 29.6 m descent, chosen by scanning the bundled tiles for the
# shortest high-relief leg near home (the suite pays for every extra kilometre
# at speedup 1). Ground: home 402.0, WP1 403.6, WP2 374.0 m AMSL.
#
# DESCENDING on purpose. Over rising ground a terrain-following failure flies
# the aircraft into the hill and the scenario learns nothing from the wreck;
# over falling ground the same failure just leaves it high, which is measurable
# and safe. The discriminator is identical either way.
WP1_N, WP1_E = 700.0, 300.0
WP2_N, WP2_E = 1279.0, 989.0
LEG_START_N = 650.0         # samples north of this are on the descending leg

# A point south of the bundle (tiles are N39-41, W095-097).
OUT_OF_COVER_LAT, OUT_OF_COVER_LON = 38.5, -95.8
MISSING_TILE = "N38W096"

# Measured 2026-08-20 on this SITL, link dead, commanded 25 m:
#   ground varied 25.1 m (384.8 -> 409.8), AGL held 25.7-29.1, rel alt swung 23.6.
# Thresholds sit well inside those numbers but far outside what the WRONG
# behaviour produces: flying above-home framing would pin rel-alt near constant
# and let AGL reach ~53 m over the low end.
MIN_GROUND_VARIATION_M = 15.0
AGL_TOLERANCE_M = 8.0
MIN_REL_ALT_SWING_M = 12.0

GUARDIAN_AIRBORNE_ALT_M = 5.0   # mirrors guardian.GuardianConfig.airborne_alt_m


def _off(lat, lon, north, east):
    return h.offset(lat, lon, north, east)


def _ne(snapshot, home_lat, home_lon):
    n = (snapshot.get("lat", 0.0) - home_lat) * 111320.0
    e = ((snapshot.get("lon", 0.0) - home_lon) * 111320.0
         * math.cos(math.radians(home_lat)))
    return n, e


def _served(client) -> dict:
    """Our TERRAIN_DATA service counters, off the telemetry surface."""
    return h.telem(client).get("terrain") or {}


def _prepare_terrain(client):
    """Make the vehicle willing to use terrain, and prove OUR service is what
    filled it. Set explicitly rather than trusting the firmware default: this
    SITL happens to boot TERRAIN_ENABLE=1/SPACING=100, but a Cube that did not
    would make every assertion below vacuous."""
    for name, value in (("TERRAIN_ENABLE", 1), ("TERRAIN_SPACING", 100)):
        r = client.post("/api/vehicle/params", json={"name": name, "value": value})
        assert r.status_code == 200, (
            f"{name} did not take: {r.status_code} {r.text}")

    loaded = h.wait_for(
        client,
        lambda t: (t.get("terrain_spacing")
                   and (t.get("terrain") or {}).get("grids_sent", 0) > 0),
        90, "the GCS serves terrain and the vehicle reports a grid spacing")

    served = _served(client)
    assert served.get("grids_sent", 0) > 0, (
        "the vehicle never asked us for terrain, so whatever it is flying on "
        f"came from its own cache — fresh_terrain did not take: {served}")
    assert not served.get("no_coverage"), served
    assert not served.get("spacing_mismatch"), served
    assert loaded["terrain_spacing"] == 100, loaded
    return served


def test_terrain_following_holds_agl_with_the_link_dead(client):
    # fresh_terrain: force the aircraft to get every block from US. See module
    # docstring — without this the scenario can pass on a stale cache.
    h.launch(client, speedup=SPEEDUP, fresh_eeprom=True, fresh_terrain=True)
    t = h.wait_flight_ready(client)
    home_lat, home_lon = t["home_lat"], t["home_lon"]

    _prepare_terrain(client)

    # Link loss must NOT abort the spray run — the standing AIR decision is that
    # the link is supervisory. So the aircraft flies this leg on its own.
    h.set_failsafe(client, gcs_enable=1, rc_long_action=0)

    wp1 = _off(home_lat, home_lon, WP1_N, WP1_E)
    wp2 = _off(home_lat, home_lon, WP2_N, WP2_E)
    r = client.post("/api/mission/upload", json={"items": [
        {"command": "TAKEOFF", "lat": home_lat, "lon": home_lon, "alt": 40},
        {"command": "WAYPOINT", "lat": wp1[0], "lon": wp1[1],
         "alt": SPRAY_AGL_M, "frame": "terrain"},
        {"command": "WAYPOINT", "lat": wp2[0], "lon": wp2[1],
         "alt": SPRAY_AGL_M, "frame": "terrain"},
    ]})
    assert r.status_code == 200, f"terrain mission refused: {r.status_code} {r.text}"
    assert "terrain" in r.json().get("frames", []), r.json()

    h.force_arm(client)
    h.start_mission(client)
    h.wait_for(client, lambda s: s["armed"], 30, "vehicle armed")
    h.wait_for(client, lambda s: s["altitude"] > 30, 180, "climb-out above 30m")

    # --- ground station goes silent and issues NO further commands. ---
    h.inject_fault(client, "gcs_link", enable=True)
    h.wait_for(client, lambda s: s["gcs_hb_suppressed"], 15, "our heartbeat stopped")
    grids_at_cut = _served(client).get("grids_sent")

    samples = []

    def reached_wp2(s):
        n, e = _ne(s, home_lat, home_lon)
        if n >= LEG_START_N and s.get("terrain_current_height") is not None:
            g = (s.get("guardian") or {})
            samples.append({
                "n": n, "e": e,
                "rel_alt": s.get("altitude"),
                "ground": s.get("terrain_height"),
                "agl": s.get("terrain_current_height"),
                "hb_suppressed": s.get("gcs_hb_suppressed"),
                "airborne": ((g.get("monitors") or {}).get("airspeed")
                             or {}).get("airborne"),
            })
        return math.hypot(n - WP2_N, e - WP2_E) < 90.0

    h.wait_for(client, reached_wp2, 300,
               "aircraft flies the descending leg to WP2 with no GCS",
               interval=0.5)

    assert len(samples) >= 10, f"too few samples on the leg to judge: {samples}"

    grounds = [s["ground"] for s in samples]
    agls = [s["agl"] for s in samples]
    rels = [s["rel_alt"] for s in samples]
    ground_var = max(grounds) - min(grounds)
    rel_swing = max(rels) - min(rels)

    # It really was linkless the whole way.
    assert all(s["hb_suppressed"] for s in samples), (
        "the link came back mid-leg — this run did not test linkless flight")

    # The premise of the measurement: the ground actually moved.
    assert ground_var >= MIN_GROUND_VARIATION_M, (
        f"the ground only varied {ground_var:.1f} m along this leg, so holding "
        f"AGL and holding above-home are indistinguishable and this scenario "
        f"proves nothing. Expected >= {MIN_GROUND_VARIATION_M} m "
        f"({min(grounds):.0f}-{max(grounds):.0f} m AMSL).")

    # 1. Height above GROUND was held.
    worst = max(abs(a - SPRAY_AGL_M) for a in agls)
    assert worst <= AGL_TOLERANCE_M, (
        f"terrain following did not hold AGL: commanded {SPRAY_AGL_M} m, saw "
        f"{min(agls):.1f}-{max(agls):.1f} m over ground that moved "
        f"{ground_var:.1f} m. Measured 25.7-29.1 m when this was written.")

    # 2. Height above HOME was NOT held — which is what rules out the aircraft
    #    having quietly flown the leg as above-home framing. Asserting only (1)
    #    would pass over flat ground; asserting only (2) would pass on a plain
    #    descent. Together they are only satisfiable by terrain following.
    assert rel_swing >= MIN_REL_ALT_SWING_M, (
        f"height above home barely changed ({rel_swing:.1f} m) while the ground "
        f"moved {ground_var:.1f} m — the aircraft is holding altitude above the "
        f"LAUNCH POINT, not above the ground. A terrain-framed item is being "
        f"flown as MAV_FRAME_GLOBAL_RELATIVE_ALT.")

    # 3. And it did it on its own: we served nothing after going silent.
    assert _served(client).get("grids_sent") == grids_at_cut, (
        "the GCS served terrain blocks AFTER the link was cut, so this leg was "
        "not flown on the aircraft's own cache and proves less than it claims")

    # --- RECORDED HAZARD (see module docstring). Not a failure of terrain
    # following; a consequence of it that the guardian has not caught up with.
    lowest_rel = min(rels)
    if lowest_rel < GUARDIAN_AIRBORNE_ALT_M:
        agl_there = min(s["agl"] for s in samples
                        if s["rel_alt"] == lowest_rel)
        assert agl_there > 20.0, agl_there
        # The geometry is the point and stays true whatever guardian does with
        # it. If guardian is ever taught to use terrain height, update the note
        # in the module docstring — this assertion needs no change.
        assert lowest_rel < GUARDIAN_AIRBORNE_ALT_M < agl_there, (
            "expected the falling ground to push height-above-home below the "
            "guardian's airborne threshold while height-above-ground stayed "
            f"high: rel {lowest_rel:.1f} m, AGL {agl_there:.1f} m")

    events = h.recent_events(client, 400)
    assert h.has_event(events, "sim", "gcs_heartbeat_suppressed")


def test_leaving_bundled_coverage_fails_loud(client):
    """Outside the bundled tiles the answer is a refusal that names the tile —
    never a silent demotion to above-home framing, and never a guessed height.

    This is where Tabor's bundling decision is cashed in: 'planning or flying
    outside bundled coverage FAILS LOUD. A missing tile must never degrade
    silently to no-terrain-awareness.'
    """
    h.launch(client, speedup=SPEEDUP, fresh_eeprom=True)
    t = h.wait_flight_ready(client)
    home_lat, home_lon = t["home_lat"], t["home_lon"]

    outside = {"command": "WAYPOINT", "lat": OUT_OF_COVER_LAT,
               "lon": OUT_OF_COVER_LON, "alt": SPRAY_AGL_M}
    takeoff = {"command": "TAKEOFF", "lat": home_lat, "lon": home_lon, "alt": 40}

    # Terrain-framed and out of coverage: refused, and the message has to be
    # actionable on a tailgate — the tile name and how to get it.
    r = client.post("/api/mission/upload",
                    json={"items": [takeoff, {**outside, "frame": "terrain"}]})
    assert r.status_code == 400, (
        f"a terrain mission outside coverage was ACCEPTED ({r.status_code}) — "
        f"the aircraft would fly it believing the ground is flat: {r.text}")
    detail = str(r.json().get("detail", ""))
    assert MISSING_TILE in detail, detail
    assert "make_terrain" in detail, (
        f"the refusal names the problem but not the fix: {detail}")

    # The SAME waypoint without a terrain claim is fine. The refusal must be
    # specific to "hold height above ground we do not have", not a blanket ban
    # on flying beyond the tiles.
    r = client.post("/api/mission/upload", json={"items": [takeoff, outside]})
    assert r.status_code == 200, (
        "a RELATIVE-framed mission outside terrain coverage was refused; the "
        f"coverage check has leaked outside the terrain frame: {r.text}")

    # And on the wire: an aircraft asking about ground we do not have gets
    # silence and a recorded refusal, not an invented height.
    before = _served(client).get("no_coverage", 0)
    # GET /api/mission/terrain?lat=&lon= is the public way to poke the aircraft
    # with a TERRAIN_CHECK; it answers about the field you are about to fly
    # rather than wherever it last looked.
    r = client.get(f"/api/mission/terrain?lat={OUT_OF_COVER_LAT}"
                   f"&lon={OUT_OF_COVER_LON}")
    assert r.status_code == 200, r.text
    assert r.json()["bundle"]["covers_point"] is False, r.json()

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        served = _served(client)
        if served.get("no_coverage", 0) > before:
            break
        time.sleep(0.5)
    served = _served(client)
    assert served.get("no_coverage", 0) > before, (
        f"the aircraft asked for terrain outside the bundle and nothing "
        f"recorded a refusal: {served}")
    refusal = served.get("last_refusal") or {}
    assert refusal.get("reason") == "no_coverage", refusal
    assert MISSING_TILE in str(refusal.get("detail", "")), refusal

    events = h.recent_events(client, 400)
    assert h.has_event(events, "terrain", "refused"), (
        "an out-of-coverage refusal never reached the event log, so an operator "
        "reviewing the flight would never learn the aircraft asked")

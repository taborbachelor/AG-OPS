"""Scenario: enforcement with the link deliberately dead.

The whole AIR premise is that the aircraft obeys and survives with ZERO ground
contact -- the radio is supervisory, not a control link. Everything else in the
SITL suite proves things while we are talking to the vehicle. These two prove
the opposite: we cut our own heartbeat and then send NO commands at all, so
whatever happens next is the flight controller acting alone.

Both halves of the onboard safety set are exercised:

  1. The exclusion FENCE stops it entering a mapped powerline corridor, while
     the mission is actively commanding it straight through one.
  2. Link-loss RTL diverts to a RALLY point instead of flying home.

Posture note -- the two tests deliberately configure FS_LONG_ACTN differently,
and that is the point rather than an inconsistency:

  * Test 1 runs FS_LONG_ACTN=0 (mission CONTINUES through link loss). This is
    the ag posture the project's own rule states -- link loss is routine and
    must not abort a spray run. It also makes the test honest: with the failsafe
    declining to intervene, the exclusion fence is the ONLY thing between the
    aircraft and the wire, so a pass cannot be credited to the failsafe.
  * Test 2 runs FS_LONG_ACTN=1 (RTL), which is what makes a rally point mean
    anything at all -- rally points only ever change where an RTL goes.

WHAT THESE TESTS CAUGHT (2026-08-19, measured against SITL, not reasoned):

  * A polygon exclusion fence was uploaded, echo-verified and held by the FC --
    and never enforced, because nothing in the product ever set FENCE_ENABLE,
    and the one endpoint that sets FENCE_TYPE hardcoded 3 (alt|circle), CLEARING
    the polygon bit that fresh firmware boots with. Enabling the geofence was
    the single act that disarmed the operator's surveyed powerlines. Both halves
    of `test_fence_turns_it_away` are the regression guard: it arms the fence
    through the product's own API and asserts the FC reports polygon enforcement
    on, then proves a breach is actually acted on.
  * POST /api/safety/keepouts returned 500 whenever a rally candidate omitted
    `break_alt` -- its own documented default. The unit tests passed plain dicts
    with the key ABSENT; the HTTP model always emits the key carrying None, and
    float(None) raised. Test 2 posts a candidate WITHOUT break_alt on purpose.

DOCUMENTED FIRMWARE DEPENDENCY: rally diversion relies on RALLY_INCL_HOME=0
(measured as the SITL default), which makes RTL prefer the nearest rally point
over home. The product never writes that parameter. A flight controller that
shipped with RALLY_INCL_HOME=1 would fly home through the hazard instead, and
test 2 is what would notice.
"""
import math

import pytest

from tests.sitl import harness as h

pytestmark = [pytest.mark.sitl, pytest.mark.slow]

# Speedup 1 throughout: the GCS failsafe times heartbeats in SIM seconds and we
# send them in WALL seconds, so link-loss behaviour only measures truthfully in
# real time (same constraint as test_scenario_link_loss).
SPEEDUP = 1.0

# Geometry, in metres north/east of home. The hazard sits on the SECOND mission
# leg on purpose: the TAKEOFF leg runs straight ahead on the launch heading, so
# a hazard placed on the home->waypoint straight line is not actually flown
# through -- an earlier cut of this scenario skirted the ring by 11 m and proved
# nothing. By leg two the aircraft is established in cruise and flies the leg.
LEG_N = 700.0            # north leg: home -> WP1
HAZ_E = 450.0            # hazard centre, east offset (on the WP1 -> WP2 leg)
HAZ_SIZE = 200.0         # square side
WP2_E = 900.0            # WP2 is beyond the hazard, so the leg transits it
RALLY_N, RALLY_E = -500.0, -500.0   # opposite side of home from the hazard

# Big enough that the circle/altitude fences cannot fire and take credit for what
# the POLYGON fence did. An ag field is bigger than the 300 m default anyway.
FENCE_RADIUS_M = 5000.0
FENCE_ALT_MAX_M = 300.0

# Measured 2026-08-19: the aircraft penetrated 63.5 m into this 200 m ring before
# FENCE_ACTION turned it out. Asserting it never crosses the ring's centreline
# (100 m) keeps ~36 m of margin while still distinguishing "turned away" from
# "flew through and carried on".
MAX_PENETRATION_M = HAZ_SIZE / 2.0


def _hazard_ring(home_lat, home_lon):
    """A closed square 'powerline corridor' straddling the second mission leg."""
    c_lat, c_lon = h.offset(home_lat, home_lon, LEG_N, HAZ_E)
    half = HAZ_SIZE / 2.0
    coords = []
    for n, e in [(-half, -half), (-half, half), (half, half), (half, -half)]:
        la, lo = h.offset(c_lat, c_lon, n, e)
        coords.append({"lat": la, "lon": lo})
    coords.append(coords[0])              # gis_zones rings are closed
    return {"kind": "powerline", "coords": coords}


def _ne(snapshot, home_lat, home_lon):
    """Aircraft position as metres north/east of home."""
    n = (snapshot.get("lat", 0.0) - home_lat) * 111320.0
    e = ((snapshot.get("lon", 0.0) - home_lon) * 111320.0
         * math.cos(math.radians(home_lat)))
    return n, e


def _penetration_m(n, e):
    """How far INSIDE the hazard square the aircraft is; 0 when outside."""
    half = HAZ_SIZE / 2.0
    dn = half - abs(n - LEG_N)
    de = half - abs(e - HAZ_E)
    return min(dn, de) if (dn > 0 and de > 0) else 0.0


def _said(client, needle: str) -> bool:
    """Did the VEHICLE say this? Read from the event log rather than the
    telemetry statustext buffer, which is a rolling window a slow poll can miss.
    """
    needle_l = needle.lower()
    return any(e.get("event") == "statustext"
               and needle_l in str(e.get("text", "")).lower()
               for e in h.recent_events(client, 500))


def _arm_hazards(client, home_lat, home_lon, rally: bool):
    """Push the hazard ring (and optionally a rally point) to the aircraft, then
    verify the FC actually holds them. A send is not proof; an echo is (M1b)."""
    body = {"zones": [_hazard_ring(home_lat, home_lon)], "hazard_buffer_m": 20}
    if rally:
        r_lat, r_lon = h.offset(home_lat, home_lon, RALLY_N, RALLY_E)
        # break_alt deliberately OMITTED -- this is the documented default path
        # and it used to 500 through the HTTP model. See the module docstring.
        body["rally_points"] = [{"lat": r_lat, "lon": r_lon, "alt": 80}]

    r = client.post("/api/safety/keepouts", json=body)
    assert r.status_code == 200, f"keepout load failed: {r.status_code} {r.text}"
    out = r.json()
    assert out["hazards"] == 1, out
    assert out["fence"].get("ok") is True, f"fence upload failed: {out['fence']}"

    held = client.get("/api/safety/exclusions").json()
    assert held["points"] == 4, f"FC does not hold the exclusion ring: {held}"

    if rally:
        assert out["rally"].get("ok") is True, f"rally upload failed: {out['rally']}"
        held_rally = client.get("/api/safety/rally").json()
        assert held_rally["points"] == 1, f"FC does not hold a rally point: {held_rally}"


def _enforce_fence(client):
    """Arm the fence through the PRODUCT'S OWN API, then confirm the flight
    controller reports polygon enforcement actually on.

    The read-back is the regression guard. Uploading rings and switching the
    fence on used to leave polygon enforcement OFF, and every surface in the
    product still reported success -- a fence that is held but never acted on.
    """
    r = client.post("/api/safety/geofence", json={
        "enable": True, "radius": FENCE_RADIUS_M,
        "alt_max": FENCE_ALT_MAX_M, "action": 1})
    assert r.status_code == 200, f"geofence enable failed: {r.status_code} {r.text}"

    live = client.get("/api/safety/geofence").json()
    assert live["enable"] is True, live
    assert live["polygon"] is True, (
        "the vehicle is not enforcing polygon exclusions -- FENCE_TYPE "
        f"{live['type']} has no polygon bit. The surveyed hazard rings are "
        f"stored and inert: {live}")


def test_fence_turns_it_away_with_the_link_dead(client):
    """The mission commands it through a mapped powerline; nobody is watching;
    the onboard fence has to be what stops it."""
    h.launch(client, speedup=SPEEDUP, fresh_eeprom=True)
    t = h.wait_flight_ready(client)
    home_lat, home_lon = t["home_lat"], t["home_lon"]

    # Link loss must NOT abort the mission -- so the fence is the only guard.
    h.set_failsafe(client, gcs_enable=1, rc_long_action=0)
    _arm_hazards(client, home_lat, home_lon, rally=False)
    _enforce_fence(client)

    wp1 = h.offset(home_lat, home_lon, LEG_N, 0)
    wp2 = h.offset(home_lat, home_lon, LEG_N, WP2_E)
    h.upload_mission(client, [
        {"command": "TAKEOFF", "lat": home_lat, "lon": home_lon, "alt": 60},
        {"command": "WAYPOINT", "lat": wp1[0], "lon": wp1[1], "alt": 60},
        {"command": "WAYPOINT", "lat": wp2[0], "lon": wp2[1], "alt": 60},
    ])
    h.force_arm(client)
    h.start_mission(client)
    h.wait_for(client, lambda s: s["armed"], 30, "vehicle armed")
    h.wait_for(client, lambda s: s["altitude"] > 40, 180, "climb-out above 40m")

    # --- from here the ground station goes silent and issues NO commands. ---
    h.inject_fault(client, "gcs_link", enable=True)
    h.wait_for(client, lambda s: s["gcs_hb_suppressed"], 15, "our heartbeat stopped")

    # Track the deepest penetration while waiting, so a failure says how far in
    # it got rather than just "no RTL".
    worst = {"m": 0.0}

    def turned_away(s):
        n, e = _ne(s, home_lat, home_lon)
        worst["m"] = max(worst["m"], _penetration_m(n, e))
        return s["mode"] == "RTL"

    breached = h.wait_for(
        client, turned_away, 240,
        "the flight controller turns the aircraft out of the exclusion ring "
        "(FENCE_ACTION -> RTL) with no GCS attached",
        interval=0.5)

    # It must have been the FENCE that acted, and it must have acted while we
    # were genuinely off the air -- otherwise this proves nothing about linkless
    # flight. Both are asserted, not assumed.
    assert breached["gcs_hb_suppressed"], (
        "the link came back before the fence acted -- this run did not test "
        f"linkless enforcement: {breached.get('mode')}")
    assert _said(client, "polygon fence breached"), (
        "the vehicle changed mode but never reported a polygon fence breach, so "
        "something other than the exclusion fence caused this RTL")

    # Turned out, rather than transited and carried on to WP2.
    assert worst["m"] < MAX_PENETRATION_M, (
        f"the aircraft penetrated {worst['m']:.0f} m into a {HAZ_SIZE:.0f} m "
        f"hazard ring -- past its centreline. Measured 63.5 m when this "
        f"scenario was written; a large increase means the fence is being "
        f"detected late, not that the threshold is wrong.")

    # And the mission is abandoned, not resumed into the wire.
    h.wait_for(client, lambda s: s["mode"] == "RTL", 30, "still holding RTL")

    events = h.recent_events(client, 400)
    assert h.has_event(events, "fence", "upload")
    assert h.has_event(events, "sim", "gcs_heartbeat_suppressed")


def test_link_loss_rtl_diverts_to_the_rally_point(client):
    """With the GCS gone the aircraft brings itself back -- to the surveyed
    alternate, not straight home through whatever the mission was avoiding."""
    h.launch(client, speedup=SPEEDUP, fresh_eeprom=True)
    t = h.wait_flight_ready(client)
    home_lat, home_lon = t["home_lat"], t["home_lon"]

    # Here link loss DOES command RTL -- rally points only change where RTL goes.
    h.set_failsafe(client, gcs_enable=1, rc_long_action=1)
    _arm_hazards(client, home_lat, home_lon, rally=True)

    h.takeoff(client, alt=70)
    h.wait_for(client, lambda s: s["armed"], 30, "vehicle armed")
    h.wait_for(client, lambda s: s["altitude"] > 50, 180, "climb-out above 50m")

    # --- ground station goes silent; no further commands are issued. ---
    h.inject_fault(client, "gcs_link", enable=True)
    h.wait_for(client, lambda s: s["gcs_hb_suppressed"], 15, "our heartbeat stopped")

    rtl = h.wait_for(client, lambda s: s["mode"] == "RTL", 90,
                     "vehicle self-commands RTL on GCS loss")
    assert rtl["gcs_hb_suppressed"], "the link returned before RTL -- not a linkless run"

    def near_rally(s):
        n, e = _ne(s, home_lat, home_lon)
        return math.hypot(n - RALLY_N, e - RALLY_E) < 150.0

    arrived = h.wait_for(
        client, near_rally, 300,
        "aircraft converges on the rally point rather than home", interval=0.5)

    n, e = _ne(arrived, home_lat, home_lon)
    d_rally = math.hypot(n - RALLY_N, e - RALLY_E)
    d_home = math.hypot(n, e)
    # Measured 2026-08-19: 114 m from rally, 599 m from home.
    assert d_rally < d_home, (
        f"link-loss RTL went home ({d_home:.0f} m) rather than to the rally "
        f"point ({d_rally:.0f} m). If the FC has RALLY_INCL_HOME=1 it will "
        f"prefer home -- the product never writes that parameter.")

    events = h.recent_events(client, 400)
    assert h.has_event(events, "sim", "gcs_heartbeat_suppressed")

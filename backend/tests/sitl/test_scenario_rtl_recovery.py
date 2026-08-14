"""Scenario: rtl-recovery — operator aborts mid-mission, then resumes.

The abort-and-come-home path must be instant and obedient (RTL on command,
measurably converging on home), and the mission must be resumable afterwards
— the spray-job pattern for "something looked off, circle home, all clear,
finish the job."
"""
import time

import pytest

from tests.sitl import harness as h

pytestmark = [pytest.mark.sitl, pytest.mark.slow]

SPEEDUP = 5.0


def test_commanded_rtl_then_mission_resume(client):
    ready = h.launch(client, speedup=SPEEDUP, fresh_eeprom=True)
    lat = ready.get("home_lat") or ready["lat"]
    lon = ready.get("home_lon") or ready["lon"]

    # Long outbound legs so there's plenty of mission left when we abort.
    wps = [h.offset(lat, lon, 800, 0), h.offset(lat, lon, 800, 800),
           h.offset(lat, lon, 0, 800)]
    h.upload_mission(client, [
        {"command": "TAKEOFF", "lat": lat, "lon": lon, "alt": 40},
        *({"command": "WAYPOINT", "lat": w[0], "lon": w[1], "alt": 60} for w in wps),
        {"command": "RTL", "lat": lat, "lon": lon, "alt": 0},
    ])
    h.start_mission(client)
    h.force_arm(client)
    h.wait_for(client, lambda t: t["armed"], 30, "vehicle armed")
    h.wait_for(client, lambda t: t["mission_seq"] >= 2 and t["altitude"] > 30, 240,
               "mid-mission (first leg active, airborne)")
    seq_at_abort = h.telem(client)["mission_seq"]

    # ABORT: commanded RTL must take effect and measurably converge on home.
    h.set_mode(client, "RTL")
    h.wait_for(client, lambda t: t["mode"] == "RTL", 15, "mode is RTL")
    d0 = h.dist_home_m(h.wait_for(client, lambda t: t["mode"] == "RTL", 10, "RTL held"))
    time.sleep(12)  # ~60 sim-seconds of flying at speedup 5
    d1 = h.dist_home_m(h.telem(client))
    assert d1 < d0 or d1 < 300, \
        f"RTL not converging on home: {d0:.0f}m -> {d1:.0f}m"

    # ALL CLEAR: resume the mission. ArduPlane continues from the active item,
    # so the job finishes rather than restarting.
    h.start_mission(client)
    h.wait_for(client, lambda t: t["mode"] == "AUTO", 15, "back in AUTO")
    resumed = h.wait_for(client, lambda t: t["mission_seq"] >= seq_at_abort, 60,
                         "mission resumed at/after the aborted item")
    assert resumed["armed"], "vehicle should still be flying"
    h.wait_for(client, lambda t: t["mission_seq"] > seq_at_abort or
               h.dist_home_m(t) < 300, 420,
               "mission progressing past the abort point (or final RTL home)")

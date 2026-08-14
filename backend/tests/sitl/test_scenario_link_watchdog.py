"""Scenario: link-watchdog — the SITL process dies under the backend.

This is the backend's own resilience story (the vehicle can't help here):
the watchdog must declare the link LOST within its 5s budget, auto-reconnect
must engage, and when the vehicle comes back the link must return to READY
by itself — no operator action, no backend restart. This is the first time
this path is proven against a real dying process instead of a fake.
"""
import pytest

from tests.sitl import harness as h

pytestmark = [pytest.mark.sitl, pytest.mark.slow]


def test_backend_survives_vehicle_death_and_reconnects(client):
    h.start_sim(client, speedup=1.0, fresh_eeprom=True)
    h.connect(client)
    h.wait_for(client, lambda t: t["connected"] and t["link_state"] == "READY",
               30, "initial link READY")

    # Kill the vehicle out from under the backend.
    h.stop_sim(client)
    h.wait_for(client, lambda t: not t["connected"], 20,
               "watchdog declares the link lost")
    h.wait_for(client, lambda t: t["reconnecting"], 15,
               "auto-reconnect engaged")

    # Vehicle returns (reconnect loop: 20 attempts x 5s — plenty of window).
    h.start_sim(client, speedup=1.0, fresh_eeprom=True)
    h.wait_for(client, lambda t: t["connected"] and t["link_state"] == "READY",
               60, "link self-healed to READY after vehicle returned")

    # Audit trail: loss, reconnect attempts, and the new connection all logged.
    events = h.recent_events(client, 300)
    assert h.has_event(events, "link", "link_lost")
    assert h.has_event(events, "link", "reconnecting")
    assert h.has_event(events, "link", "connected")

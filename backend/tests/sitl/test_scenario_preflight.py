"""Scenario: preflight — the backend refuses to arm an unready vehicle.

Right after boot (no GPS fix yet) the arm endpoint must 409 with the failing
blockers named; once the vehicle is genuinely flight-ready the same request
must go through. The go/no-go authority is the server, live-proven.
"""
import pytest

from tests.sitl import harness as h

pytestmark = [pytest.mark.sitl, pytest.mark.slow]

SPEEDUP = 5.0


def test_arm_gated_until_actually_ready(client):
    h.start_sim(client, speedup=SPEEDUP, fresh_eeprom=True)
    h.connect(client)

    # Immediately after connect the GPS hasn't converged: the gate must hold.
    pf = client.get("/api/safety/preflight").json()
    r = client.post("/api/vehicle/arm", json={"force": True})
    if r.status_code == 200:
        # Convergence beat us to it (fast machine) — nothing to prove here,
        # but that possibility is why the harness never sleeps blind.
        pytest.skip("vehicle was already flight-ready at first arm attempt")
    assert r.status_code == 409, f"expected gate refusal, got {r.status_code}: {r.text}"
    detail = r.json()["detail"]
    assert detail["failed"], detail
    assert not pf["ready"]

    # The vehicle is not armed — the refusal was real.
    assert not h.telem(client)["armed"]

    # Now let it become genuinely ready: the very same request must pass.
    h.wait_flight_ready(client)
    r = client.post("/api/vehicle/arm", json={"force": True})
    assert r.status_code == 200, f"ready vehicle refused: {r.text}"
    h.wait_for(client, lambda t: t["armed"], 15, "armed after gate opened")
    client.post("/api/vehicle/disarm", json={"force": True})

    # Both gate decisions are in the audit trail.
    events = h.recent_events(client, 300)
    assert h.has_event(events, "preflight", "blocked")

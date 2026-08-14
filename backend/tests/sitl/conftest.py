"""Fixtures for the SITL scenario harness.

One TestClient per scenario, torn down defensively: disconnect the vehicle
link, kill the SITL, and wait for TCP 5760 to actually free so the next
scenario's spawn can't collide with a dying process. Scenarios are strictly
serial (they share one port and one vehicle_manager singleton).
"""
import time

import pytest
from fastapi.testclient import TestClient

from app.guardian import GuardianConfig, config_to_dict
from app.main import app
from app.vehicle_manager import vehicle_manager


def _sim_stopped(client, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if not client.get("/api/sim/status").json().get("running"):
                return True
        except Exception:
            return True
        time.sleep(0.5)
    return False


@pytest.fixture()
def client():
    with TestClient(app) as c:
        try:
            yield c
        finally:
            # Always leave a clean world, even when the scenario failed midway.
            try:
                c.post("/api/connection/disconnect")
            except Exception:
                pass
            try:
                c.post("/api/sim/stop")
            except Exception:
                pass
            # Scenarios share one vehicle_manager singleton per pytest process:
            # a scenario that tuned the guardian must not leak into the next.
            vehicle_manager.set_guardian_config(config_to_dict(GuardianConfig()))
            # SITL also exits on client disconnect; give the port time to free.
            _sim_stopped(c)
            time.sleep(1.0)

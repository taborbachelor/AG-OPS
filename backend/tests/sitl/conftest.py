"""Fixtures for the SITL scenario harness.

One TestClient per scenario, bracketed by a REAL port check at both ends:

  - setup refuses to start unless this instance's SITL port is actually free,
  - teardown waits for it to free instead of sleeping a flat second and hoping.

Both halves exist because of a measured failure: with only a `/api/sim/status`
poll and a 1 s sleep, scenarios run alongside a second session failed 3, 4 and 5
at a time with a DIFFERENT set each run, always at the connection level
(`no heartbeat from vehicle`, WinError 10061/10054) and never on an assertion —
a scenario connecting to a SITL that had not finished dying. A red scenario
should say "the port was busy" when that is what happened, not look like a
regression in the code under test.

Scenarios are strictly serial within a process: they share one port and one
vehicle_manager singleton. To run a second suite on the same machine, give it
its own port with SITL_INSTANCE=<n> (see app/config.py) — that offsets every
port SITL binds, so the two runs never meet.
"""
import time
import warnings

import pytest
from fastapi.testclient import TestClient

from app.guardian import GuardianConfig, config_to_dict
from app.main import app
from app.routers import sim
from app.vehicle_manager import vehicle_manager

from . import harness as h

PORT_FREE_TIMEOUT = h.PORT_FREE_TIMEOUT


def _sim_stopped(client, timeout: float = 15.0) -> bool:
    """Poll until /api/sim/status stops reporting `running`.

    Best-effort only — the authoritative answer is the port check that follows,
    which is why an unreachable status endpoint just ends the poll rather than
    being treated as proof of anything.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if not client.get("/api/sim/status").json().get("running"):
                return True
        except Exception:
            return False
        time.sleep(0.5)
    return False


def _require_free_port():
    if not h.port_is_free(PORT_FREE_TIMEOUT):
        pytest.fail(h.port_contention_message())


@pytest.fixture()
def client():
    # Before anything: prove the port is ours. Doing this in SETUP is what makes
    # the guarantee survive a previous scenario that crashed, timed out, or was
    # interrupted — none of which run a teardown.
    _require_free_port()
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
            # SITL also exits on client disconnect. Wait for the port to actually
            # clear; warn rather than fail, because the scenario that just ran is
            # not the one at fault — the NEXT one's setup will stop hard, which
            # puts the failure where it belongs.
            _sim_stopped(c)
            if not h.port_is_free(PORT_FREE_TIMEOUT):
                warnings.warn(
                    f"SITL port {sim.SITL_PORT} did not free within "
                    f"{PORT_FREE_TIMEOUT:.0f}s of teardown; the next scenario "
                    f"will refuse to start. Look for a leaked ArduPlane process.",
                    stacklevel=1)
            # A short settle on top of the port check, deliberately kept from the
            # original teardown. "No LISTENING socket" is not quite "the kernel
            # has let go": a lingering TIME_WAIT from the closed GCS connection
            # can still refuse the next bind on Windows. The check above is the
            # proof; this is the margin. Drop it only with a green full-suite run
            # behind the decision.
            time.sleep(1.0)

"""Built-in SITL simulator manager.

Lets the GCS start/stop the bundled ArduPlane SITL itself, so the Simulator
Quick Connect button works with zero terminals. Also neutralizes the classic
gotcha (this SITL build exits when the GCS disconnects): reconnecting simply
respawns it.

SITL location search order:
  - packaged exe:  <dir of the exe>/sitl/ArduPlane.exe
  - dev checkout:  rc-plane-app/sitl/ArduPlane.exe
"""
import subprocess
import sys
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

router = APIRouter()

SITL_PORT = 5760
# Same home as run_sitl.bat: Sabetha, KS (lat, lon, alt_m, heading)
SITL_ARGS = ["-M", "plane", "-O", "39.9042,-95.7997,408,0", "--speedup", "1"]

_proc: subprocess.Popen | None = None


def _sitl_exe() -> Path | None:
    if getattr(sys, "frozen", False):
        candidates = [Path(sys.executable).resolve().parent / "sitl" / "ArduPlane.exe"]
    else:
        # backend/app/routers/sim.py -> parents[3] == rc-plane-app
        candidates = [Path(__file__).resolve().parents[3] / "sitl" / "ArduPlane.exe"]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _port_listening() -> bool:
    """Passively check whether something is LISTENING on the SITL port.

    Must NOT probe by connecting: this SITL build exits when a client
    disconnects, so a connect-and-close readiness probe kills the very
    simulator it's checking on (found the hard way).
    """
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, timeout=10, creationflags=flags,
        )
        suffix = f":{SITL_PORT}"
        for line in out.stdout.splitlines():
            parts = line.split()
            if (len(parts) >= 4 and parts[0] == "TCP"
                    and parts[1].endswith(suffix) and parts[3] == "LISTENING"):
                return True
        return False
    out = subprocess.run(["ss", "-ltn"], capture_output=True, text=True, timeout=10)
    return f":{SITL_PORT}" in out.stdout


def _running() -> bool:
    global _proc
    if _proc is not None and _proc.poll() is not None:
        _proc = None  # reaped: it exited (normal after a GCS disconnect)
    # _proc covers SITL we spawned; the port check covers one started by hand
    # (run_sitl.bat) so we don't double-launch.
    return _proc is not None or _port_listening()


def _start_blocking() -> dict:
    global _proc
    if _running():
        return {"status": "already_running"}
    exe = _sitl_exe()
    if exe is None:
        raise HTTPException(status_code=404, detail="SITL binary not found (sitl/ArduPlane.exe)")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    _proc = subprocess.Popen(
        [str(exe), *SITL_ARGS],
        cwd=exe.parent,  # eeprom.bin / terrain live next to the binary
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    # Wait for the TCP listener (fast) — GPS/EKF convergence (~30s) happens after
    # connect and is surfaced in the UI as usual.
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if _proc.poll() is not None:
            _proc = None
            raise HTTPException(status_code=500, detail="SITL exited immediately after launch")
        if _port_listening():
            return {"status": "started"}
        time.sleep(0.5)
    raise HTTPException(status_code=500, detail="SITL did not open TCP 5760 within 20s")


@router.get("/status")
def sim_status():
    return {"available": _sitl_exe() is not None, "running": _running()}


@router.post("/start")
async def sim_start():
    return await run_in_threadpool(_start_blocking)


@router.post("/stop")
def sim_stop():
    global _proc
    if _proc is not None:
        _proc.terminate()
        _proc = None
        return {"status": "stopped"}
    return {"status": "not_running"}

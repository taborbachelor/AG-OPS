"""Built-in SITL simulator manager + M4 fault injection.

Lets the GCS start/stop the bundled ArduPlane SITL itself, so the Simulator
Quick Connect button works with zero terminals. Also neutralizes the classic
gotcha (this SITL build exits when the GCS disconnects): reconnecting simply
respawns it.

M4 additions for the scenario harness:
  - POST /start accepts {speedup, fresh_eeprom}: speedup compresses sim time
    for scripted scenarios; fresh_eeprom runs SITL in an isolated scratch dir
    with no eeprom.bin so every scenario starts from firmware defaults WITHOUT
    touching the demo eeprom that lives next to the binary.
  - POST /fault injects/clears faults on the running vehicle:
      gps       -> SIM_GPS1_ENABLE=0 (4.5+ name) or SIM_GPS_DISABLE=1 (legacy)
      battery   -> SIM_BATT_VOLTAGE sagged to `value` (restored on clear)
      gcs_link  -> suppress our own 1Hz GCS heartbeat (vehicle sees GCS loss)
      gps_noise -> SIM_GPS1_HNSE raised: the position solution degrades while
                   the EKF flags stay HEALTHY, which is the only way to drive
                   the guardian's EKF *variance* monitor (the plain `gps`
                   fault flips the flags outright and takes the other branch)
      airspeed  -> SIM_ARSPD_FAIL pins the pitot to a stuck low reading
                   (blockage/icing), driving the stall-margin monitor
    All param-based faults go through the verified (M1b) write path, so a
    fault that didn't actually reach the vehicle fails loudly.

    THERE IS DELIBERATELY NO `vibration` FAULT. See the note below.

SITL location search order:
  - packaged exe:  <dir of the exe>/sitl/ArduPlane.exe
  - dev checkout:  rc-plane-app/sitl/ArduPlane.exe
"""
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.eventlog import log_event
from app.vehicle_manager import vehicle_manager

router = APIRouter()

SITL_PORT = 5760
# Same home as run_sitl.bat: Sabetha, KS (lat, lon, alt_m, heading)
SITL_HOME = "39.9042,-95.7997,408,0"
# Subdir (next to the binary) used for fresh_eeprom runs, so scenario runs
# never mutate the demo eeprom.bin that lives beside ArduPlane.exe.
SCENARIO_DIR_NAME = "_scenario"

_proc: subprocess.Popen | None = None
# Options of the currently running (router-spawned) SITL, for /status.
_run_info: dict | None = None
# Active injected faults.
_faults: dict = {"gps": False, "battery": False, "gcs_link": False,
                 "gps_noise": False, "airspeed": False}
# Pre-fault values, keyed by param name, so clearing a fault restores what the
# vehicle actually had rather than a guessed default. Captured once per fault
# (re-injecting without clearing must not overwrite the original with a
# faulted value) and popped on clear.
_prev: dict[str, float] = {}

# (name, faulted_value, healthy_value) — ArduPilot renamed the GPS-disable
# knob across firmware versions (same story as SYSID_MYGCS/MAV_GCS_SYSID):
# 4.5+ has SIM_GPS1_ENABLE (1=on, fault = 0), older has SIM_GPS_DISABLE
# (0=off, fault = 1). We use whichever the connected firmware actually has.
GPS_FAULT_PARAMS = (
    ("SIM_GPS1_ENABLE", 0.0, 1.0),
    ("SIM_GPS_DISABLE", 1.0, 0.0),
)
# SITL's fully-charged default battery voltage, used as the restore value if
# the pre-fault voltage was never observed.
SIM_BATT_HEALTHY_V = 12.6
DEFAULT_FAULT_BATT_V = 9.8

# GPS horizontal noise (m) for the gps_noise fault. 10 m drives the EKF's
# reported pos_horiz_variance to ~2-4 (guardian warns at 0.6) while
# ekf_healthy stays True; 2 m stays UNDER the threshold, which makes it a
# usable negative control in a scenario. Measured on this SITL 2026-08-18.
DEFAULT_GPS_NOISE_M = 10.0

# Stuck pitot reading (m/s) for the airspeed fault. Below the guardian's
# stall floor (8 m/s default) so the monitor is genuinely exercised.
# NOTE: ArduPilot runs its own airspeed-health check and DISABLES a sensor it
# judges implausible (~1.4 s in, "Airspeed sensor 1 failure. Disabling"), then
# re-enables it when the reading returns — so the reported airspeed oscillates
# rather than sitting low. A scenario can rely on the WARNING appearing, but
# must not assume a multi-second sustained low reading.
DEFAULT_FAULT_ARSPD_MS = 4.0

# NO VIBRATION FAULT — measured, not assumed (2026-08-18).
#
# The guardian's vibration/clip monitor cannot be driven from this SITL build,
# so no fault is offered for it: an endpoint that writes params which change
# nothing would report "fault injected" while the vehicle is unaffected, which
# is exactly the silent lie the verified-write path exists to prevent.
#
# What was tried, in flight at 80 m, across five flights: SIM_VIB_MOT_MAX /
# MULT / MASK / HMNC, SIM_VIB_FREQ_X/Y/Z, SIM_ACC1_RND, SIM_ACCEL1_FAIL,
# SIM_ACC1_SCAL_*. Every one verified as WRITTEN and none moved the reported
# VIBRATION levels: steady flight sits at ~0.17 m/s/s with the params set to
# their extremes exactly as it does with them at zero. (An apparent 4x
# response in an early probe turned out to be airframe dynamics after
# level-off — that same run's clean baseline peaked at 0.562, HIGHER than any
# "injected" reading.) SIM_VIB_MOT_* appears to model MULTICOPTER motor
# vibration; a fixed-wing frame has no motors in that list.
#
# Accelerometer clipping never occurs either. The monitor therefore stays
# unit-tested only, recorded as a known gap in SPRAY-FLIGHT-SAFETY.md Part 3C
# rather than papered over with a scenario that proves nothing.


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


def _build_args(speedup: float) -> list[str]:
    return ["-M", "plane", "-O", SITL_HOME, "--speedup", str(speedup)]


def _resolve_cwd(exe: Path, fresh_eeprom: bool) -> Path:
    """Working dir for the SITL process (it reads/writes eeprom.bin in cwd).

    Normal runs use the binary's own dir (persistent demo eeprom + terrain).
    fresh_eeprom runs use an isolated scratch subdir with any previous
    eeprom.bin deleted, so the vehicle boots with pure firmware defaults and
    scenario runs are deterministic — and the demo eeprom is never touched.
    """
    if not fresh_eeprom:
        return exe.parent
    scratch = exe.parent / SCENARIO_DIR_NAME
    scratch.mkdir(exist_ok=True)
    for leftover in ("eeprom.bin",):
        try:
            (scratch / leftover).unlink(missing_ok=True)
        except OSError:
            pass
    return scratch


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


def _reset_faults():
    """Forget fault bookkeeping (used on start/stop: a new or dead SITL has no
    injected faults) and make sure our own heartbeat is not left silenced."""
    for k in _faults:
        _faults[k] = False
    _prev.clear()
    vehicle_manager.set_gcs_heartbeat_suppressed(False)


def _remember(name: str, fallback: float) -> None:
    """Record a param's pre-fault value the FIRST time it is faulted.

    Re-injecting a fault that is already active must not capture the faulted
    value as the "previous" one — that would make clearing restore the fault.
    """
    if name in _prev:
        return
    cached = vehicle_manager.cached_value(name)
    _prev[name] = float(cached) if cached is not None else fallback


def _restore(name: str, fallback: float) -> float:
    return _prev.pop(name, fallback)


class SimStartRequest(BaseModel):
    # 1.0 = real time (UI default). Scenario runs compress sim time; capped so
    # a typo can't outrun the physics loop.
    speedup: float = Field(1.0, ge=0.5, le=20)
    fresh_eeprom: bool = False


def _start_blocking(req: SimStartRequest) -> dict:
    global _proc, _run_info
    if _running():
        return {"status": "already_running", **(_run_info or {})}
    exe = _sitl_exe()
    if exe is None:
        raise HTTPException(status_code=404, detail="SITL binary not found (sitl/ArduPlane.exe)")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    _proc = subprocess.Popen(
        [str(exe), *_build_args(req.speedup)],
        cwd=_resolve_cwd(exe, req.fresh_eeprom),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    _run_info = {"speedup": req.speedup, "fresh_eeprom": req.fresh_eeprom}
    _reset_faults()
    log_event("sim", "sitl_started", speedup=req.speedup,
              fresh_eeprom=req.fresh_eeprom)
    # Wait for the TCP listener (fast) — GPS/EKF convergence (~30s) happens after
    # connect and is surfaced in the UI as usual.
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if _proc.poll() is not None:
            _proc = None
            raise HTTPException(status_code=500, detail="SITL exited immediately after launch")
        if _port_listening():
            return {"status": "started", **_run_info}
        time.sleep(0.5)
    raise HTTPException(status_code=500, detail="SITL did not open TCP 5760 within 20s")


@router.get("/status")
def sim_status():
    return {"available": _sitl_exe() is not None, "running": _running(),
            "run": _run_info, "faults": dict(_faults)}


@router.post("/start")
async def sim_start(req: SimStartRequest = SimStartRequest()):
    return await run_in_threadpool(_start_blocking, req)


@router.post("/stop")
def sim_stop():
    global _proc, _run_info
    _reset_faults()
    if _proc is not None:
        _proc.terminate()
        _proc = None
        _run_info = None
        log_event("sim", "sitl_stopped")
        return {"status": "stopped"}
    _run_info = None
    return {"status": "not_running"}


# --- M4 fault injection -----------------------------------------------------

class FaultRequest(BaseModel):
    fault: Literal["gps", "battery", "gcs_link", "gps_noise", "airspeed"]
    enable: bool = True
    # Magnitude of the fault, for the faults that take one:
    #   battery   -> sagged pack voltage (V)
    #   gps_noise -> GPS horizontal noise (m)
    #   airspeed  -> stuck pitot reading (m/s)
    # Each branch range-checks its own value below, and param_meta carries the
    # authoritative curated range for every SIM_* param actually written.
    value: Optional[float] = Field(None, ge=0, le=100)


def _require_vehicle():
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected to a vehicle")


def _require_sitl():
    """Refuse fault injection on anything that isn't demonstrably SITL.

    The gcs_link fault silences our GCS heartbeat: on a REAL vehicle with
    FS_GCS enabled that triggers an actual RTL. SITL firmware exposes SIM_*
    parameters and real firmware doesn't, so the synced param cache is the
    discriminator; if the cache hasn't synced yet, probe one SIM param live
    rather than guessing."""
    cached = vehicle_manager.get_cached_params()
    if any(n.startswith("SIM_") for n in cached):
        return
    if cached:
        # Cache is populated and has no SIM_* params: this is real firmware.
        raise HTTPException(409, "Fault injection refused: connected vehicle "
                                 "is not a SITL simulator")
    try:
        probe = vehicle_manager.get_param("SIM_SPEEDUP")
    except Exception:
        probe = None
    if probe is None:
        raise HTTPException(409, "Fault injection refused: cannot confirm the "
                                 "connected vehicle is a SITL simulator")


def _verified_set(name: str, value: float) -> dict:
    """Write a fault param through the normal verified path; loud on failure so
    a scenario can never believe a fault is active when the vehicle never got
    it (the exact class of silent lie M1b exists to prevent)."""
    res = vehicle_manager.set_param(name, value)
    if res.get("rejected"):
        raise HTTPException(422, {"message": f"{name} rejected by validation",
                                  "error": res.get("error")})
    if not res.get("verified"):
        raise HTTPException(502, {"message": f"Vehicle did not confirm {name}",
                                  "error": res.get("error")})
    return res


def _gps_fault_param() -> tuple[str, float, float]:
    """Pick whichever GPS-disable knob this firmware exposes, preferring the
    param cache (synced on connect); fall back to trying candidates with a
    verified write of their HEALTHY value (a no-op on the vehicle)."""
    for name, faulted, healthy in GPS_FAULT_PARAMS:
        if vehicle_manager.cached_type(name) is not None:
            return name, faulted, healthy
    for name, faulted, healthy in GPS_FAULT_PARAMS:
        try:
            cur = vehicle_manager.get_param(name)
        except Exception:
            cur = None
        if cur is not None:
            return name, faulted, healthy
    raise HTTPException(502, {"message": "no GPS fault param found on vehicle",
                              "tried": [n for n, _, _ in GPS_FAULT_PARAMS]})


def _check_value(fault: str, value: float, lo: float, hi: float) -> None:
    """Per-fault magnitude bound (the model's own field bound is the union)."""
    if not (lo <= value <= hi):
        raise HTTPException(
            422, f"{fault} fault value must be in {lo:g}..{hi:g}")


def _inject_fault(req: FaultRequest) -> dict:
    _require_vehicle()
    _require_sitl()
    detail: dict = {}

    if req.fault == "gps":
        name, faulted, healthy = _gps_fault_param()
        res = _verified_set(name, faulted if req.enable else healthy)
        detail = {"param": name, "value": res["accepted"]}

    elif req.fault == "battery":
        if req.enable:
            volts = req.value if req.value is not None else DEFAULT_FAULT_BATT_V
            # Validate BEFORE remembering: a rejected injection must leave no
            # bookkeeping behind at all.
            _check_value("battery", volts, 0.0, 100.0)
            _remember("SIM_BATT_VOLTAGE", SIM_BATT_HEALTHY_V)
        else:
            volts = _restore("SIM_BATT_VOLTAGE", SIM_BATT_HEALTHY_V)
        res = _verified_set("SIM_BATT_VOLTAGE", volts)
        detail = {"param": "SIM_BATT_VOLTAGE", "value": res["accepted"]}

    elif req.fault == "gps_noise":
        # Degrade the fix WITHOUT killing it: the EKF keeps a position
        # solution (flags stay healthy) while its reported variance climbs,
        # which is the branch the `gps` fault can never reach.
        if req.enable:
            noise = req.value if req.value is not None else DEFAULT_GPS_NOISE_M
            _check_value("gps_noise", noise, 0.0, 100.0)
            _remember("SIM_GPS1_HNSE", 0.0)
        else:
            noise = _restore("SIM_GPS1_HNSE", 0.0)
        res = _verified_set("SIM_GPS1_HNSE", noise)
        detail = {"param": "SIM_GPS1_HNSE", "value": res["accepted"]}

    elif req.fault == "airspeed":
        # Pitot pinned to a stuck low reading (blockage/icing). 0 clears it —
        # that IS the param's healthy value, so there is nothing to restore.
        if req.enable:
            speed = (req.value if req.value is not None
                     else DEFAULT_FAULT_ARSPD_MS)
            _check_value("airspeed", speed, 0.0, 100.0)
        else:
            speed = 0.0
        res = _verified_set("SIM_ARSPD_FAIL", speed)
        detail = {"param": "SIM_ARSPD_FAIL", "value": res["accepted"]}

    else:  # gcs_link — no param: we silence our own heartbeat.
        vehicle_manager.set_gcs_heartbeat_suppressed(req.enable)
        detail = {"gcs_hb_suppressed": req.enable}

    _faults[req.fault] = req.enable
    log_event("sim", "fault_injected" if req.enable else "fault_cleared",
              level="WARN" if req.enable else "INFO", fault=req.fault, **detail)
    return {"status": "ok", "fault": req.fault, "enable": req.enable, **detail}


@router.post("/fault")
def sim_fault(req: FaultRequest):
    return _inject_fault(req)

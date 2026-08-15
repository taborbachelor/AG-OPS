from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from app import preflight
from app.eventlog import log_event
from app.vehicle_manager import vehicle_manager

router = APIRouter()

# NOTE: handlers that talk to the vehicle are deliberately plain `def`, not
# `async def`: FastAPI runs them in its threadpool, so a slow/blocking
# pymavlink call (serial dial, 30s heartbeat wait, param download, ack waits)
# can never freeze the event loop — emergency commands (RTL, disarm) and the
# telemetry WebSocket must stay responsive at all times.


@router.websocket("/rc")
async def rc_override_ws(ws: WebSocket):
    """Receive a stream of {channels:[...]} from a laptop-connected transmitter/
    gamepad and forward each as an RC override. Releases the override on
    disconnect so the plane never keeps stuck stick positions."""
    await ws.accept()
    try:
        while True:
            data = await ws.receive_json()
            # One malformed frame (or the link dropping mid-send) must NEVER
            # kill this handler: the pilot's stick input would silently stop
            # working while the socket still looks open. Skip bad frames and
            # keep reading.
            try:
                channels = data.get("channels") if isinstance(data, dict) else None
                if isinstance(channels, (list, tuple)) and vehicle_manager.connected:
                    vehicle_manager.send_rc_override(list(channels))
            except Exception:
                continue
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        vehicle_manager.release_rc_override()


class ModeRequest(BaseModel):
    mode: str


@router.get("/modes")
async def get_modes():
    return {"modes": vehicle_manager.get_available_modes()}


@router.post("/mode")
def set_mode(req: ModeRequest):
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    # set_mode() is ack-checked: False means the vehicle doesn't know the mode
    # or refused the change. Never tell the operator "ok" for a mode change
    # that didn't happen (e.g. they think the plane is landing and it isn't).
    if not vehicle_manager.set_mode(req.mode):
        raise HTTPException(400, f"Vehicle rejected mode change to {req.mode}")
    return {"status": "ok", "mode": req.mode}


class ArmRequest(BaseModel):
    force: bool = False      # bypass ArduPilot's own pre-arm checks (21196)
    override: bool = False   # bypass the GCS-side pre-flight gate (M6)


class TakeoffRequest(BaseModel):
    alt: float = Field(100.0, gt=0, le=2000)
    force: bool = False
    override: bool = False


def _preflight_gate(override: bool, command: str):
    """M6: the backend, not the UI, is the go/no-go authority. Refuse arming
    while a pre-flight BLOCKER fails, unless the operator explicitly
    overrides — and either way, record the decision."""
    pf = preflight.evaluate(vehicle_manager.snapshot(),
                            vehicle_manager.cached_value("FENCE_ENABLE"))
    if pf["ready"]:
        return
    if override:
        log_event("preflight", "override", level="WARN", command=command,
                  failed_blockers=pf["failed_blockers"])
        return
    log_event("preflight", "blocked", level="WARN", command=command,
              failed_blockers=pf["failed_blockers"])
    failed = [c for c in pf["checks"] if c["blocker"] and not c["ok"]]
    raise HTTPException(409, {
        "message": "pre-flight blockers failing — vehicle NOT armed",
        "failed": [f"{c['label']} ({c['detail']})" if c["detail"] else c["label"]
                   for c in failed],
        "hint": "fix the blockers or arm with override=true",
    })


@router.post("/arm")
def arm(req: ArmRequest = ArmRequest()):
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    _preflight_gate(req.override, "arm")
    result = vehicle_manager.arm(force=req.force)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "arming failed"))
    return {"status": "armed", **result}


@router.post("/disarm")
def disarm(req: ArmRequest = ArmRequest()):
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    result = vehicle_manager.disarm(force=req.force)
    if not result.get("ok"):
        # Never report "disarmed" when the vehicle rejected or didn't ack the
        # disarm — the prop may still be spinning.
        raise HTTPException(400, result.get("error") or "disarm failed")
    return {"status": "disarmed", **result}


@router.post("/takeoff")
def takeoff(req: TakeoffRequest = TakeoffRequest()):
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    _preflight_gate(req.override, "takeoff")
    result = vehicle_manager.takeoff(alt=req.alt, force=req.force)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "takeoff failed"))
    return {"status": "takeoff", **result}


@router.post("/land")
def land():
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    result = vehicle_manager.land()
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "landing failed"))
    return {"status": "landing", **result}


@router.get("/params")
def get_all_params(cached: bool = False):
    """Full parameter table. `cached=true` returns the M3 cache instantly (from
    the on-connect sync) without touching the link; otherwise it runs a fresh
    sync (a few seconds; telemetry pauses while the link is dedicated to it)."""
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    if cached:
        params = vehicle_manager.get_cached_params()
        return {"params": params, "count": len(params), "cached": True,
                "sync": vehicle_manager._param_sync}
    params = vehicle_manager.get_all_params()
    if not params:
        raise HTTPException(504, "Parameter download timed out")
    return {"params": params, "count": len(params), "cached": False}


@router.post("/params/sync")
def sync_params():
    """Kick off a fresh full parameter sync in the background. Poll progress via
    telemetry's `param_sync` field."""
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    vehicle_manager.sync_params()
    return {"status": "syncing", "sync": vehicle_manager._param_sync}


@router.get("/params/backup")
def backup_params():
    """Full parameter backup in Mission Planner .param format (NAME,VALUE
    lines) from the synced cache — take one before every bench session and
    before the first flight, so any change is reversible."""
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    params = vehicle_manager.get_cached_params()
    sync = vehicle_manager.snapshot().get("param_sync") or {}
    if not params or not sync.get("synced"):
        raise HTTPException(409, {
            "message": "parameter cache not fully synced — backup would be "
                       "incomplete", "sync": sync})
    caps = vehicle_manager.snapshot().get("capabilities") or {}
    lines = [f"# {len(params)} params, fw {caps.get('fw_version', '?')}, "
             f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}"]
    lines += [f"{n},{v:.6g}" for n, v in sorted(params.items())]
    return PlainTextResponse("\n".join(lines) + "\n",
                             media_type="text/plain",
                             headers={"Content-Disposition":
                                      'attachment; filename="backup.param"'})


class ParamRestore(BaseModel):
    content: str  # .param file text: NAME,VALUE per line, # comments ignored


def _is_volatile(name: str) -> bool:
    """Params the firmware live-estimates (writing them back can never verify
    — found on the harness: BARO*_GND_PRESS re-estimates continuously) or that
    are runtime statistics. A restore skips these, like Mission Planner does."""
    return name.endswith("_GND_PRESS") or name.startswith("STAT_")


@router.post("/params/restore")
def restore_params(req: ParamRestore):
    """Write a .param backup to the vehicle: only params that differ from the
    cache are written (each one verified); every outcome is reported."""
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    cache = vehicle_manager.get_cached_params()
    wanted, malformed = {}, []
    for ln in req.content.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        parts = ln.replace("\t", ",").split(",")
        try:
            wanted[parts[0].strip().upper()] = float(parts[1])
        except (IndexError, ValueError):
            malformed.append(ln)
    if not wanted:
        raise HTTPException(422, "no NAME,VALUE lines found")
    written, failed, skipped, unknown, volatile = [], [], [], [], []
    for name, value in wanted.items():
        if name not in cache:
            unknown.append(name)
            continue
        if _is_volatile(name):
            volatile.append(name)
            continue
        if abs(cache[name] - value) < 1e-6:
            skipped.append(name)
            continue
        res = vehicle_manager.set_param(name, value)
        (written if res.get("verified") else failed).append(name)
    log_event("bench", "params_restored", written=len(written),
              failed=failed, unknown=len(unknown), volatile=len(volatile))
    return {"status": "ok" if not failed else "partial",
            "written": written, "failed": failed, "skipped_same": len(skipped),
            "skipped_volatile": volatile, "unknown_on_vehicle": unknown,
            "malformed_lines": malformed}


class ParamUpdate(BaseModel):
    name: str
    value: float


@router.post("/params")
def set_param(req: ParamUpdate):
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    result = vehicle_manager.set_param(req.name, req.value)
    # Metadata validation (M3): an out-of-range / wrong-type value never went to
    # the vehicle — reject it as a bad request, distinct from a link failure.
    if result.get("rejected"):
        raise HTTPException(422, {
            "message": f"{req.name} rejected by validation",
            "param": req.name, "requested": result["requested"],
            "error": result.get("error"),
        })
    # Echo-verified (M1b): surface what the FC actually stored, and fail loudly
    # if it didn't take — never report a blind success.
    if not result["verified"]:
        raise HTTPException(502, {
            "message": f"Vehicle did not confirm {req.name}",
            "param": req.name,
            "requested": result["requested"],
            "accepted": result["accepted"],
            "error": result.get("error"),
        })
    return {
        "status": "ok",
        "param": req.name,
        "requested": result["requested"],
        "value": result["accepted"],
        "verified": True,
    }

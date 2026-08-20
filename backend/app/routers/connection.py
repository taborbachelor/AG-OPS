"""Connection router: port enumeration, manual connect, and USB auto-connect.

Auto-connect exists because COM numbering is not stable. The same Cube is COM3
on one boot and COM7 on the next -- a different USB socket, a hub, a Windows
driver reshuffle -- so a remembered port number is worthless and the operator
ends up guessing from a dropdown in a field. The USB vendor id does not move.
"""
import re
import threading
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import serial.tools.list_ports
from app.vehicle_manager import vehicle_manager

router = APIRouter()


class ConnectRequest(BaseModel):
    connection_string: str
    baud: int = 57600


class AutoConnectRequest(BaseModel):
    # Override only for the bench. Left None, a matched board dials the rate
    # its own entry declares.
    baud: Optional[int] = None


# --- USB identification --------------------------------------------------
#
# The Cube (Hex / ProfiCNC) enumerates with USB vendor id 0x2DAE. A VID is
# assigned to the board vendor, so matching it answers "is this a Cube" without
# caring what COM number Windows handed it this time.
#
# Deliberately narrow. No telemetry radio is listed -- a SiK radio is an FTDI
# (0x0403), Silicon Labs (0x10C4) or 3DR (0x26AC) USB-serial bridge, and what
# sits on the far end of it is unknown to us. Auto-dialling one at the Cube's
# 115200 would open the wrong link at the wrong rate to an unidentified
# vehicle. Add a VID here only with that board in hand to test it.
CUBE_VID = 0x2DAE

FLIGHT_CONTROLLER_VIDS = {
    CUBE_VID: "Cube (Hex/ProfiCNC)",
}

# Direct USB to the Cube runs at 115200 (CLAUDE-CALEB, hardware procedures).
# USB CDC ignores the line rate, but pymavlink still applies it and a serial
# link opened at the wrong rate is the classic silent no-heartbeat failure.
USB_FC_BAUD = 115200
# Telemetry-radio default, and what the manual form has always offered.
DEFAULT_BAUD = 57600

# Auto-connect is single-flight. Handlers here are plain `def`, so FastAPI runs
# them in a threadpool and two overlapping POSTs would both pass the
# `connected` check and both dial -- clobbering the link and leaving duplicate
# telemetry loops. Not hypothetical: React StrictMode double-fires effects in
# development, so the UI issues exactly that pair on mount.
_autoconnect_lock = threading.Lock()


def _natural_key(device: str):
    """COM10 sorts after COM2. A plain string sort puts it first, which would
    make "the first Cube" depend on how many digits the port number has."""
    d = device or ""
    m = re.search(r"(\d+)$", d)
    if m:
        return (d[:m.start()].upper(), int(m.group(1)))
    return (d.upper(), -1)


def _port_info(p) -> dict:
    """One enumerated serial port. `device`/`description` are the original
    shape and must stay -- the UI has always read those two. Everything else
    is additive."""
    vid = getattr(p, "vid", None)
    board = FLIGHT_CONTROLLER_VIDS.get(vid)
    return {
        "device": p.device,
        "description": p.description,
        "vid": vid,
        "pid": getattr(p, "pid", None),
        "serial_number": getattr(p, "serial_number", None),
        "manufacturer": getattr(p, "manufacturer", None),
        "board": board,
        "is_flight_controller": board is not None,
        "suggested_baud": USB_FC_BAUD if board else DEFAULT_BAUD,
    }


def _scan_ports():
    """Returns (all ports, flight-controller ports) — the latter in a stable
    order so repeated auto-connects pick the same board."""
    ports = [_port_info(p) for p in serial.tools.list_ports.comports()]
    fcs = sorted((p for p in ports if p["is_flight_controller"]),
                 key=lambda p: _natural_key(p["device"]))
    return ports, fcs


# NOTE: blocking handlers are plain `def` (FastAPI runs them in a threadpool)
# so a slow serial dial or 30s heartbeat wait can never freeze the event loop
# and with it the whole GCS API (RTL/disarm/telemetry).


@router.get("/ports")
def list_serial_ports():
    ports, _ = _scan_ports()
    return ports


@router.get("/detect")
def detect_flight_controller():
    """What auto-connect would do, without doing it. Always 200 — this is a
    question, and "no Cube plugged in" is a valid answer, not an error."""
    ports, fcs = _scan_ports()
    best = fcs[0] if fcs else None
    return {
        "found": best is not None,
        "device": best["device"] if best else None,
        "board": best["board"] if best else None,
        "baud": best["suggested_baud"] if best else None,
        "candidates": fcs,
        "ports": ports,
    }


@router.post("/connect")
def connect_vehicle(req: ConnectRequest):
    if vehicle_manager.connected:
        raise HTTPException(400, "Already connected. Disconnect first.")
    if vehicle_manager.reconnecting:
        # A manual connect racing the auto-reconnect thread's own connect()
        # would clobber the connection and spawn duplicate telemetry loops.
        raise HTTPException(409, "Auto-reconnect in progress. Disconnect first to cancel it.")
    try:
        vehicle_manager.connect(req.connection_string, req.baud)
        return {"status": "connected", "connection": req.connection_string}
    except ConnectionError as e:
        raise HTTPException(500, str(e))


@router.post("/autoconnect")
def autoconnect_vehicle(req: AutoConnectRequest = AutoConnectRequest()):
    """Find the Cube by USB VID and link to it without asking.

    Already connected is a 200, not an error: the UI fires this on startup and
    a repeat must be a no-op rather than something the operator has to read.
    """
    if not _autoconnect_lock.acquire(blocking=False):
        raise HTTPException(409, "Auto-connect already in progress.")
    try:
        if vehicle_manager.connected:
            return {"status": "already_connected",
                    "connection": vehicle_manager.connection_string}
        if vehicle_manager.reconnecting:
            raise HTTPException(
                409, "Auto-reconnect in progress. Disconnect first to cancel it.")

        ports, candidates = _scan_ports()
        if not candidates:
            seen = ", ".join(f"{p['device']} ({p['description']})"
                             for p in ports) or "none"
            # Name what WAS seen: on a bench that message is the whole
            # diagnosis (wrong cable, charge-only lead, driver missing).
            raise HTTPException(
                404,
                f"No flight controller on USB (looked for VID "
                f"0x{CUBE_VID:04X}). Ports seen: {seen}")

        attempts = []
        for cand in candidates:
            baud = req.baud or cand["suggested_baud"]
            try:
                vehicle_manager.connect(cand["device"], baud)
            except ConnectionError as e:
                # Try the next match rather than failing the whole flow: a
                # board can expose more than one USB serial interface and only
                # one of them speaks MAVLink. connect() closes its own port on
                # failure, so nothing is left holding the COM handle.
                attempts.append({"device": cand["device"], "error": str(e)})
                continue
            return {"status": "connected", "connection": cand["device"],
                    "baud": baud, "board": cand["board"], "auto": True,
                    "attempts": attempts}

        detail = "; ".join(f"{a['device']}: {a['error']}" for a in attempts)
        raise HTTPException(
            500, f"Found a flight controller but the link did not come up — {detail}")
    finally:
        _autoconnect_lock.release()


@router.post("/disconnect")
def disconnect_vehicle():
    vehicle_manager.disconnect()
    return {"status": "disconnected"}


@router.get("/status")
async def connection_status():
    return {
        "connected": vehicle_manager.connected,
        "connection_string": vehicle_manager.connection_string,
        "reconnecting": vehicle_manager.reconnecting,
        # M2: connection state machine + link identity.
        "link_state": vehicle_manager._link_state,
        "gcs_sysid": vehicle_manager._sysid,
        "vehicle_sysid": vehicle_manager._vehicle_sysid,
        "capabilities": vehicle_manager._capabilities,
    }

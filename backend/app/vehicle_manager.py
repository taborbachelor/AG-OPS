import threading
import time
import json
from collections import deque
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional
from pymavlink import mavutil

from app.eventlog import log_event
from app import config
from app import guardian
from app import param_meta

# Flight logs live next to the app package.
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

# EKF_STATUS_REPORT flags (ardupilotmega EKF_STATUS_FLAGS enum); literal
# fallbacks in case a dialect build lacks the names.
_EKF_ATTITUDE = getattr(mavutil.mavlink, "EKF_ATTITUDE", 1)
_EKF_POS_HORIZ_REL = getattr(mavutil.mavlink, "EKF_POS_HORIZ_REL", 8)
_EKF_POS_HORIZ_ABS = getattr(mavutil.mavlink, "EKF_POS_HORIZ_ABS", 16)
_EKF_CONST_POS_MODE = getattr(mavutil.mavlink, "EKF_CONST_POS_MODE", 128)
_EKF_UNINITIALIZED = getattr(mavutil.mavlink, "EKF_UNINITIALIZED", 1024)

# SYS_STATUS sensor bits we surface by name when a sensor is enabled but
# reporting unhealthy (MAV_SYS_STATUS_SENSOR_* / MAV_SYS_STATUS_* enums).
_SENSOR_BITS = {
    "gyro": getattr(mavutil.mavlink, "MAV_SYS_STATUS_SENSOR_3D_GYRO", 1),
    "accel": getattr(mavutil.mavlink, "MAV_SYS_STATUS_SENSOR_3D_ACCEL", 2),
    "mag": getattr(mavutil.mavlink, "MAV_SYS_STATUS_SENSOR_3D_MAG", 4),
    "baro": getattr(mavutil.mavlink, "MAV_SYS_STATUS_SENSOR_ABSOLUTE_PRESSURE", 8),
    "gps": getattr(mavutil.mavlink, "MAV_SYS_STATUS_SENSOR_GPS", 32),
    "rc": getattr(mavutil.mavlink, "MAV_SYS_STATUS_SENSOR_RC_RECEIVER", 65536),
    "ahrs": getattr(mavutil.mavlink, "MAV_SYS_STATUS_AHRS", 0x200000),
    "terrain": getattr(mavutil.mavlink, "MAV_SYS_STATUS_TERRAIN", 0x400000),
    "battery": getattr(mavutil.mavlink, "MAV_SYS_STATUS_SENSOR_BATTERY", 0x1000000),
}

# MAV_SEVERITY names, indexed by the STATUSTEXT severity value (0..7).
_MAV_SEVERITY = ["EMERGENCY", "ALERT", "CRITICAL", "ERROR",
                 "WARNING", "NOTICE", "INFO", "DEBUG"]

# Mission command types we support, mapped to their MAVLink NAV commands.
_CMD_TO_MAV = {
    "TAKEOFF": mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
    "WAYPOINT": mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
    "LOITER": mavutil.mavlink.MAV_CMD_NAV_LOITER_UNLIM,
    "LAND": mavutil.mavlink.MAV_CMD_NAV_LAND,
    "RTL": mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH,
}
_MAV_TO_CMD = {v: k for k, v in _CMD_TO_MAV.items()}


class LinkState:
    """The connection's lifecycle (directive M2). Plain string constants so they
    serialize straight into telemetry/WS and read cleanly in the event log.

    DISCONNECTED -> CONNECTING -> WAITING_HEARTBEAT -> SYNCHRONIZING -> READY,
    with READY <-> DEGRADED as heartbeats thin out, and -> LOST when the
    watchdog fires (auto-reconnect then drives CONNECTING again)."""
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    WAITING_HEARTBEAT = "WAITING_HEARTBEAT"
    SYNCHRONIZING = "SYNCHRONIZING"
    READY = "READY"
    DEGRADED = "DEGRADED"
    LOST = "LOST"


# MAV_PROTOCOL_CAPABILITY_* bits we decode from AUTOPILOT_VERSION, with literal
# fallbacks in case a dialect build lacks a name.
_CAP_BITS = {
    "mission_int": getattr(mavutil.mavlink, "MAV_PROTOCOL_CAPABILITY_MISSION_INT", 4),
    "command_int": getattr(mavutil.mavlink, "MAV_PROTOCOL_CAPABILITY_COMMAND_INT", 2),
    "param_float": getattr(mavutil.mavlink, "MAV_PROTOCOL_CAPABILITY_PARAM_FLOAT", 1),
    "ftp": getattr(mavutil.mavlink, "MAV_PROTOCOL_CAPABILITY_FTP", 8),
    "set_attitude_target": getattr(
        mavutil.mavlink, "MAV_PROTOCOL_CAPABILITY_SET_ATTITUDE_TARGET", 16),
    "set_position_target_global": getattr(
        mavutil.mavlink, "MAV_PROTOCOL_CAPABILITY_SET_POSITION_TARGET_GLOBAL_INT", 32),
    "flight_termination": getattr(
        mavutil.mavlink, "MAV_PROTOCOL_CAPABILITY_FLIGHT_TERMINATION", 1024),
    "mission_fence": getattr(
        mavutil.mavlink, "MAV_PROTOCOL_CAPABILITY_MISSION_FENCE", 4096),
    "mission_rally": getattr(
        mavutil.mavlink, "MAV_PROTOCOL_CAPABILITY_MISSION_RALLY", 8192),
}


@dataclass
class TelemetryData:
    armed: bool = False
    mode: str = "UNKNOWN"
    altitude: float = 0.0
    airspeed: float = 0.0
    groundspeed: float = 0.0
    heading: int = 0
    lat: float = 0.0
    lon: float = 0.0
    battery_voltage: float = 0.0
    battery_current: float = 0.0
    battery_level: Optional[int] = None
    # Charge drawn this flight (mAh), from BATTERY_STATUS when the vehicle sends it.
    battery_consumed_mah: Optional[float] = None
    pitch: float = 0.0
    roll: float = 0.0
    yaw: float = 0.0
    gps_fix: int = 0
    gps_satellites: int = 0
    # RC input: raw PWM per channel (µs, ~1000-2000) and receiver signal strength.
    rc_channels: list = field(default_factory=list)
    rc_rssi: int = 0
    # Servo/motor outputs the flight controller is actually commanding (PWM µs).
    servo_outputs: list = field(default_factory=list)
    # Mission progress: current item seq (0 = home), user-waypoint count, and
    # distance to the active waypoint in meters.
    mission_seq: int = 0
    mission_count: int = 0
    wp_dist: float = 0.0
    # Home/launch point mirrored into telemetry so the UI can compute
    # distance-to-home (RTL margin) without extra endpoints.
    home_lat: float = 0.0
    home_lon: float = 0.0
    # EKF health (EKF_STATUS_REPORT): raw flags bitmask + a conservative
    # "safe to navigate" verdict (attitude + a horizontal position solution,
    # not in constant-position fallback, not uninitialized).
    ekf_flags: int = 0
    ekf_healthy: bool = False
    # Sensors SYS_STATUS reports as enabled-but-unhealthy, by name.
    sensor_errors: list = field(default_factory=list)


class VehicleManager:
    def __init__(self):
        self.connection = None
        self.connected = False
        self.connection_string = None
        self.telemetry = TelemetryData()
        self._telemetry_thread = None
        self._running = False
        self._mode_mapping = {}
        # Home / launch point, learned from HOME_POSITION messages.
        self.home_lat = 0.0
        self.home_lon = 0.0
        self.home_alt = 0.0
        # Link watchdog: if no heartbeat arrives for this long, the vehicle link
        # is considered lost and we mark ourselves disconnected.
        self._last_heartbeat = 0.0
        self._link_timeout = 5.0
        # Vehicle-heartbeat arrival times (ring) for link-quality estimation,
        # and when this connection came up so early quality isn't penalized.
        self._hb_times = deque(maxlen=30)
        self._connected_at = 0.0
        # Recent STATUSTEXT from the vehicle (prearm failures, failsafe
        # announcements) — the messages Mission Planner shows in its HUD.
        self._statustext = deque(maxlen=50)
        # Guards telemetry state across threads: the telemetry thread writes,
        # API/WS workers read via snapshot() — never a torn multi-field view.
        self._telem_lock = threading.Lock()
        # We must also SEND heartbeats at 1Hz: ArduPilot's GCS-loss failsafe
        # (FS_GCS_ENABL) triggers RTL if the ground station goes silent —
        # discovered live when a spray mission kept snapping back to RTL.
        self._last_hb_sent = 0.0
        # Auto-reconnect: after an unexpected link loss (USB wiggle, radio
        # dropout) retry the last connection string. User-initiated
        # disconnects clear the flag so we never fight the operator.
        self._auto_reconnect = False
        self._conn_params = None
        self.reconnecting = False
        # Flight logging: one file per flight (opened on arm, closed on disarm).
        self._log_fh = None
        self._log_start = 0.0
        self._last_sample = 0.0
        # Serializes access to the MAVLink connection so the telemetry thread and
        # mission upload/download (which also read the link) don't steal each
        # other's messages. Held briefly per telemetry read, and for the whole
        # duration of a mission transaction.
        self._link_lock = threading.Lock()
        # Serializes MAVLink *sends*: pymavlink's packer (pack seq -> write ->
        # seq += 1) is not thread-safe, and the telemetry thread's heartbeat,
        # the reconnect thread and API/WS handlers all send concurrently.
        # Lock ordering: _send_lock may be taken while holding _link_lock,
        # never the other way around.
        self._send_lock = threading.Lock()
        # Bumped on every operator disconnect() so an in-flight connect()
        # (e.g. from the auto-reconnect thread) can detect that the operator
        # pulled the plug while it was dialing and must NOT bring the link up.
        self._disconnect_gen = 0
        # --- M2: link identity & state machine ---
        # Our own MAVLink identity. Default != 255 so we don't collide with
        # Mission Planner on a shared endpoint (see app/config.py).
        self._sysid = config.GCS_SYSID
        self._compid = config.GCS_COMPID
        # The vehicle's sysid/compid, locked from its first heartbeat. Once set,
        # we drop RX traffic from any other system (a 2nd GCS, another vehicle).
        self._vehicle_sysid = None
        self._vehicle_compid = None
        self._msgs_filtered = 0  # count of RX messages dropped by the sysid filter
        # Connection lifecycle state + when we entered it.
        self._link_state = LinkState.DISCONNECTED
        self._state_since = 0.0
        # READY -> DEGRADED once no heartbeat for this long (still < loss timeout).
        self._link_degraded_after = 3.0
        # AUTOPILOT_VERSION-derived capabilities (fetched on connect), or None.
        self._capabilities = None
        # --- M3: parameter engine ---
        # Full parameter table cache: name -> {value, type, index}. Populated by
        # the on-connect sync and kept live by every verified write. Guarded by
        # its own lock (API reads it while the sync thread fills it).
        self._param_cache = {}
        self._param_count = None
        self._param_lock = threading.Lock()
        self._param_sync_thread = None
        # Progress for the UI: {"syncing", "received", "total", "synced"}.
        self._param_sync = {"syncing": False, "received": 0, "total": None,
                            "synced": False}
        # --- M4: scenario-harness fault injection ---
        # When True we stop sending our 1Hz GCS heartbeat so ArduPilot's
        # GCS-loss failsafe fires on demand: the vehicle believes the ground
        # station vanished while our RX side keeps observing the reaction.
        self._suppress_gcs_hb = False
        # --- Guardian: GCS-side failsafe monitors + emergency state machine.
        # Config is operator-tunable via /api/safety/guardian; state/monitors
        # ride in snapshot() so the UI gets them over the telemetry WS free.
        self.guardian_config = guardian.GuardianConfig()
        self._guardian_lock = threading.Lock()
        self._guardian = {"state": guardian.NORMAL, "monitors": {},
                          "warnings": [], "rtl_source": None,
                          "rtl_reason": None}
        self._guardian_thread = None
        self._guardian_mem = guardian.default_memory()
        self._guardian_prev_armed = False
        self._guardian_rtl_attempts = 0
        # Set by land() so the state machine can tell LANDING from plain AUTO.
        self._landing_requested = False

    def _set_state(self, new_state: str, level: str = "INFO", **details):
        """Transition the connection state machine and log the transition.
        Idempotent: re-entering the same state is a no-op (no log spam)."""
        old = self._link_state
        if old == new_state:
            return
        self._link_state = new_state
        self._state_since = time.time()
        log_event("link", "state", level=level,
                  **{"from_state": old, "to_state": new_state, **details})

    @staticmethod
    def _link_level(age):
        """Graded link health from the last-heartbeat age (directive's 1/3/5/10s
        levels). None when disconnected."""
        if age is None:
            return None
        if age <= 1.0:
            return "good"
        if age <= 3.0:
            return "nominal"
        if age <= 5.0:
            return "degraded"
        if age <= 10.0:
            return "poor"
        return "critical"

    def _from_vehicle(self, msg) -> bool:
        """True if the message came from the connected vehicle's sysid. Before
        the vehicle sysid is locked (initial handshake) everything is accepted;
        afterwards, traffic from any other system — a second GCS sharing the
        endpoint, another vehicle — is rejected so it can't consume our ACKs,
        PARAM_VALUEs, or falsely feed the heartbeat watchdog."""
        if self._vehicle_sysid is None:
            return True
        try:
            return msg.get_srcSystem() == self._vehicle_sysid
        except Exception:
            return True

    def connect(self, connection_string: str, baud: int = 57600) -> bool:
        # Snapshot the disconnect generation so we can tell if the operator
        # called disconnect() while we were dialing / waiting for a heartbeat.
        gen = self._disconnect_gen
        conn = None
        try:
            self._set_state(LinkState.CONNECTING, connection=connection_string)
            # We announce ourselves as GCS sysid 252 (not pymavlink's default
            # 255) so we don't collide with Mission Planner on a shared endpoint.
            conn = mavutil.mavlink_connection(
                connection_string, baud=baud,
                source_system=self._sysid, source_component=self._compid)
            # Publish immediately so an operator disconnect() can close the
            # port and interrupt the heartbeat wait below.
            self.connection = conn
            self._set_state(LinkState.WAITING_HEARTBEAT)
            hb = conn.wait_heartbeat(timeout=30)
            if hb is None:
                # wait_heartbeat returns None on timeout (it does NOT raise) —
                # a silent link must never be treated as a live vehicle.
                raise ConnectionError("no heartbeat from vehicle")
            if gen != self._disconnect_gen:
                # Operator disconnected while we were connecting — honor it:
                # do not bring the link up or re-arm auto-reconnect.
                raise ConnectionError("cancelled by operator disconnect")
            self._set_state(LinkState.SYNCHRONIZING, vehicle_sysid=conn.target_system)
            # Lock onto this vehicle's identity — from here, RX filtering drops
            # everything that isn't from it.
            self._vehicle_sysid = conn.target_system
            self._vehicle_compid = conn.target_component
            # Fresh vehicle: clear any telemetry/home left over from a previous
            # connection so we never show a different vehicle's stale data.
            with self._telem_lock:
                self.telemetry = TelemetryData()
                self._statustext.clear()
            self._hb_times.clear()
            self._capabilities = None
            with self._param_lock:
                self._param_cache = {}
                self._param_count = None
                self._param_sync = {"syncing": False, "received": 0,
                                    "total": None, "synced": False}
            self.home_lat = self.home_lon = self.home_alt = 0.0
            # A fresh link must never start with a silenced GCS heartbeat left
            # over from a fault-injection scenario — the vehicle would RTL.
            self._suppress_gcs_hb = False
            self.connected = True
            self.connection_string = connection_string
            self._last_heartbeat = time.time()
            self._connected_at = self._last_heartbeat
            self._conn_params = (connection_string, baud)
            self._auto_reconnect = True
            self.reconnecting = False
            self._mode_mapping = conn.mode_mapping()
            self._request_data_streams()
            # Ask for the home position (used by the landing flow).
            with self._send_lock:
                conn.mav.command_long_send(
                    conn.target_system, conn.target_component,
                    mavutil.mavlink.MAV_CMD_GET_HOME_POSITION, 0, 0, 0, 0, 0, 0, 0, 0)
            # Make ourselves the recognized GCS BEFORE the telemetry loop starts,
            # so this lock-held param read/write has the link to itself.
            self._align_gcs_sysid()
            self._start_telemetry_loop()
            self._start_guardian()
            # Request capabilities AFTER the loop is running so the AUTOPILOT_
            # VERSION reply is routed to _handle_msg — not swallowed by the
            # align's recv loop (which discards non-PARAM_VALUE messages).
            self._request_capabilities(conn)
            # M3: full parameter sync in the background (the SYNCHRONIZING work).
            # Decoupled from READY so a slow-radio download never delays flight
            # readiness; progress is reported in snapshot()['param_sync'].
            self.sync_params()
            self._set_state(LinkState.READY, vehicle_sysid=conn.target_system,
                            gcs_sysid=self._sysid)
            log_event("link", "connected", connection=connection_string, baud=baud,
                      vehicle_sysid=conn.target_system, gcs_sysid=self._sysid)
            return True
        except Exception as e:
            self.connected = False
            self._vehicle_sysid = None
            self._vehicle_compid = None
            self._set_state(LinkState.DISCONNECTED, level="ERROR", error=str(e))
            log_event("link", "connect_failed", level="ERROR",
                      connection=connection_string, error=str(e))
            # Never leak a half-open port: on serial, a leaked handle keeps the
            # COM port busy and blocks every future (re)connect attempt until
            # the backend is restarted.
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
                if self.connection is conn:
                    self.connection = None
            raise ConnectionError(f"Failed to connect: {e}")

    def _request_data_streams(self, rate_hz: int = 10):
        """Ask the autopilot to stream telemetry. Without this, ArduPilot only
        sends heartbeats and we get no attitude/position/battery data."""
        with self._send_lock:
            self.connection.mav.request_data_stream_send(
                self.connection.target_system,
                self.connection.target_component,
                mavutil.mavlink.MAV_DATA_STREAM_ALL,
                rate_hz,
                1,  # start streaming
            )

    def _request_capabilities(self, conn):
        """Ask the autopilot for AUTOPILOT_VERSION (its protocol capabilities +
        firmware version). Fire-and-request: the reply is handled by the
        telemetry loop and lands in snapshot()['capabilities']. Best-effort —
        a vehicle that doesn't answer just leaves capabilities None."""
        try:
            with self._send_lock:
                conn.mav.command_long_send(
                    conn.target_system, conn.target_component,
                    mavutil.mavlink.MAV_CMD_REQUEST_AUTOPILOT_CAPABILITIES,
                    0, 1, 0, 0, 0, 0, 0, 0)
        except Exception as e:
            log_event("link", "capabilities_request_failed", level="WARN", error=str(e))

    def _align_gcs_sysid(self):
        """Point the vehicle's "commanding GCS" param at our GCS sysid (verified
        M1b write) so its GCS-loss failsafe counts our heartbeats. Since we moved
        off the default 255, skipping this would make ArduPilot ignore our
        heartbeats and RTL on FS_GCS — the exact failure from the project
        history. The param was renamed across firmware versions, so we align
        whichever candidate the vehicle actually has. No-op if disabled."""
        if not config.MANAGE_SYSID_MYGCS or not self.connection:
            return
        try:
            # Find which candidate this firmware exposes (a read that echoes).
            name, cur_val = None, None
            with self._link_lock:
                for cand in config.GCS_SYSID_PARAM_CANDIDATES:
                    cur = self._request_param(self.connection, cand)
                    if cur is not None:
                        name, cur_val = cand, round(cur.param_value)
                        break
            if name is None:
                log_event("link", "sysid_mygcs_param_not_found", level="WARN",
                          tried=list(config.GCS_SYSID_PARAM_CANDIDATES))
                return
            if cur_val == self._sysid:
                log_event("link", "sysid_mygcs_ok", param=name, sysid=self._sysid)
                return
            res = self.set_param(name, self._sysid)
            log_event("link", "sysid_mygcs_aligned",
                      level="INFO" if res["verified"] else "WARN",
                      param=name, previous=cur_val, requested=self._sysid,
                      accepted=res["accepted"], verified=res["verified"])
        except Exception as e:
            log_event("link", "sysid_mygcs_align_failed", level="WARN", error=str(e))

    def disconnect(self):
        # Operator asked for this — never auto-reconnect afterwards. Bump the
        # generation FIRST so any connect() currently in flight (reconnect
        # thread mid-dial) aborts instead of silently re-linking.
        self._disconnect_gen += 1
        self._auto_reconnect = False
        self.reconnecting = False
        self._running = False
        if self._telemetry_thread:
            self._telemetry_thread.join(timeout=5)
        self._close_log()
        if self.connection:
            self.connection.close()
        self.connection = None
        self.connected = False
        self.connection_string = None
        self._vehicle_sysid = None
        self._vehicle_compid = None
        self._set_state(LinkState.DISCONNECTED)
        log_event("link", "disconnected_by_operator")

    def _start_telemetry_loop(self):
        # Never run two telemetry loops at once (they would split messages and
        # fight over _running/_last_heartbeat): stop any previous loop first.
        old = self._telemetry_thread
        if old is not None and old.is_alive() and old is not threading.current_thread():
            self._running = False
            old.join(timeout=2)
        self._running = True
        self._telemetry_thread = threading.Thread(target=self._update_telemetry, daemon=True)
        self._telemetry_thread.start()

    def _send_gcs_heartbeat(self, conn):
        """Send our 1Hz GCS heartbeat if one is due. ArduPilot's GCS-loss
        failsafe (FS_GCS_ENABL) triggers RTL if the ground station goes silent,
        so every long-running link transaction must keep calling this."""
        if self._suppress_gcs_hb:
            return
        if time.time() - self._last_hb_sent > 1.0:
            try:
                with self._send_lock:
                    conn.mav.heartbeat_send(
                        mavutil.mavlink.MAV_TYPE_GCS,
                        mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0)
                self._last_hb_sent = time.time()
            except Exception:
                pass

    # --- Guardian runner -----------------------------------------------------

    def _start_guardian(self):
        """1Hz supervisor thread (started on connect, exits with the link).
        Same single-instance discipline as the telemetry loop."""
        old = self._guardian_thread
        if old is not None and old.is_alive():
            return  # loop keys off self._running/connected; it follows the link
        self._guardian_thread = threading.Thread(target=self._guardian_loop,
                                                 daemon=True)
        self._guardian_thread.start()

    def _guardian_loop(self):
        while self._running and self.connected:
            try:
                self._guardian_tick(time.time())
            except Exception as e:
                # The guardian must never take down the link it supervises.
                log_event("guardian", "tick_error", level="ERROR", error=str(e))
            time.sleep(1.0)

    def _guardian_tick(self, now: float):
        """One evaluation pass: judge telemetry, run the emergency state
        machine, and execute (or stand down from) a guardian RTL."""
        with self._telem_lock:
            t = asdict(self.telemetry)
        t["link_level"] = self._link_level(
            (now - self._last_heartbeat) if self.connected and self._last_heartbeat
            else None)
        cfg = self.guardian_config
        mem = self._guardian_mem

        # Arm rising edge: a new flight gets fresh debounce/latch memory.
        if t["armed"] and not self._guardian_prev_armed:
            mem.update(guardian.default_memory())
            self._landing_requested = False
            self._guardian_rtl_attempts = 0
        self._guardian_prev_armed = t["armed"]

        res = guardian.evaluate(cfg, t, mem, now)
        action, source, reason = res["action"], res["source"], res["reason"]
        mode = t.get("mode")

        # Operator override: we commanded RTL, the mode has left RTL, and the
        # condition still demands it -> the operator wins. Log it once and
        # stand down for this source until its condition clears.
        if (mem["rtl_commanded_for"] is not None and mode != "RTL"
                and action == "rtl" and source == mem["rtl_commanded_for"]):
            log_event("guardian", "overridden_by_operator", level="WARN",
                      source=source, mode=mode)
            mem["standdown_for"] = source
            mem["rtl_commanded_for"] = None
        # Condition cleared: re-arm the latch and any standdown.
        if action is None:
            if mem["standdown_for"] is not None:
                log_event("guardian", "standdown_cleared",
                          source=mem["standdown_for"])
            mem["standdown_for"] = None
            if mode != "RTL":
                mem["rtl_commanded_for"] = None
                self._guardian_rtl_attempts = 0

        rtl_fired = False
        if (action == "rtl" and mem["rtl_commanded_for"] is None
                and mem["standdown_for"] != source
                and self._guardian_rtl_attempts < 3):
            # Directive: record the triggering condition BEFORE the command.
            log_event("guardian", "rtl_triggered", level="ERROR",
                      source=source, reason=reason,
                      monitors=res["monitors"])
            rtl_fired = True
            if self.set_mode("RTL"):
                mem["rtl_commanded_for"] = source
                self._guardian_rtl_attempts = 0
            else:
                self._guardian_rtl_attempts += 1
                log_event("guardian", "rtl_command_failed", level="ERROR",
                          source=source, attempt=self._guardian_rtl_attempts)
                if self._guardian_rtl_attempts >= 3:
                    log_event("guardian", "rtl_gave_up", level="ERROR",
                              source=source,
                              note="vehicle kept rejecting RTL; operator action required")

        prev_state = self._guardian["state"]
        state = guardian.derive_state(
            prev_state, t["armed"], mode, res["warnings"],
            rtl_commanded=(mem["rtl_commanded_for"] is not None or rtl_fired),
            landing_requested=self._landing_requested)
        if state != prev_state:
            log_event("guardian", "state",
                      level="WARN" if state not in (guardian.NORMAL,
                                                    guardian.DISARMED) else "INFO",
                      from_state=prev_state, to_state=state,
                      warnings=res["warnings"])
        with self._guardian_lock:
            self._guardian = {
                "state": state, "monitors": res["monitors"],
                "warnings": res["warnings"],
                "rtl_source": mem["rtl_commanded_for"],
                "rtl_reason": reason if mem["rtl_commanded_for"] else None,
            }

    def set_guardian_config(self, updates: dict) -> dict:
        """Apply operator-tuned guardian settings (already schema-validated by
        the router). Logged like any other safety-relevant change."""
        cfg = self.guardian_config
        for k, v in updates.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        log_event("guardian", "config_changed", **updates)
        return guardian.config_to_dict(cfg)

    def guardian_state(self) -> dict:
        with self._guardian_lock:
            return dict(self._guardian)

    def set_gcs_heartbeat_suppressed(self, suppressed: bool):
        """M4 fault injection: silence/restore our 1Hz GCS heartbeat. With
        FS_GCS_ENABL set on the vehicle, suppression reproduces a ground-station
        loss exactly (FS_SHORT/FS_LONG escalate to RTL) while our receive path
        stays up so the harness can watch the vehicle react. Never persists
        across connections — connect() clears it."""
        if self._suppress_gcs_hb == bool(suppressed):
            return
        self._suppress_gcs_hb = bool(suppressed)
        log_event("sim", "gcs_heartbeat_suppressed" if suppressed
                  else "gcs_heartbeat_restored",
                  level="WARN" if suppressed else "INFO")

    def _recv_blocking(self, conn, types, timeout: float):
        """Wait up to `timeout` seconds for a message whose type is in `types`.

        Replacement for recv_match(type=..., blocking=True) inside lock-held
        transactions: recv_match with a type filter silently consumes and
        DISCARDS every other message — including HEARTBEATs — so any long
        transaction (mission transfer on a slow radio, param reads, ack waits)
        would starve the link watchdog and tear down a perfectly healthy link
        the moment the lock is released. Here we read unfiltered, bump the
        watchdog on every HEARTBEAT that passes by, and keep sending our own
        1Hz GCS heartbeat so the vehicle's GCS failsafe never fires either."""
        if isinstance(types, str):
            types = [types]
        deadline = time.time() + timeout
        while conn is not None:
            self._send_gcs_heartbeat(conn)
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            msg = conn.recv_match(blocking=True, timeout=min(remaining, 1.0))
            if msg is None:
                continue
            # Same RX sysid filter as the telemetry loop: a co-connected GCS's
            # HEARTBEAT must not bump our watchdog, and its PARAM_VALUE/
            # COMMAND_ACK must not be mistaken for our transaction's reply.
            if not self._from_vehicle(msg):
                self._msgs_filtered += 1
                continue
            msg_type = msg.get_type()
            if msg_type == "HEARTBEAT":
                self._last_heartbeat = time.time()
                self._hb_times.append(self._last_heartbeat)
            if msg_type in types:
                return msg
        return None

    def _update_telemetry(self):
        while self._running and self.connection:
            # Link watchdog, checked every iteration up front so it fires whether
            # the read returns no data OR raises (a reset socket raises, which is
            # exactly what happens when the vehicle/SITL goes away).
            age = time.time() - self._last_heartbeat
            if age > self._link_timeout:
                self._on_link_lost()
                break
            # Graded state: warn (DEGRADED) once heartbeats thin out but before
            # the link is declared lost, and recover to READY when they return.
            if self._link_state == LinkState.READY and age > self._link_degraded_after:
                self._set_state(LinkState.DEGRADED, level="WARN",
                                last_heartbeat_age=round(age, 2))
            elif self._link_state == LinkState.DEGRADED and age <= self._link_degraded_after:
                self._set_state(LinkState.READY)
            # Announce ourselves at 1Hz so the vehicle's GCS-loss failsafe
            # never fires because of us.
            conn = self.connection
            if conn is not None:
                self._send_gcs_heartbeat(conn)
            try:
                with self._link_lock:
                    msg = self.connection.recv_match(blocking=False)
                if msg is None:
                    time.sleep(0.01)
                    continue
                # RX sysid filter: ignore traffic from any system that isn't the
                # connected vehicle (a co-connected Mission Planner, another
                # vehicle) so it can't feed our watchdog or corrupt telemetry.
                if not self._from_vehicle(msg):
                    self._msgs_filtered += 1
                    continue
                self._handle_msg(msg)
            except Exception:
                # Socket error / decode error: don't spin hot; the watchdog
                # below will mark us disconnected if heartbeats stop.
                time.sleep(0.05)

    def _handle_msg(self, msg):
        """Apply one MAVLink message to the telemetry state. Split out of the
        loop so tests can drive it with fake messages. Telemetry writes happen
        under _telem_lock so snapshot() never sees a torn multi-field update."""
        msg_type = msg.get_type()

        if msg_type == "HEARTBEAT":
            now = time.time()
            self._last_heartbeat = now
            self._hb_times.append(now)
            mode = mavutil.mode_string_v10(msg)
            armed = (msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED) != 0
            with self._telem_lock:
                prev_mode, prev_armed = self.telemetry.mode, self.telemetry.armed
                self.telemetry.mode = mode
                self.telemetry.armed = armed
            # Vehicle-truth transitions (mode change, arm state) are flight
            # events regardless of who commanded them — log from here.
            if mode != prev_mode:
                log_event("vehicle", "mode_changed", mode=mode, prev=prev_mode)
            if armed != prev_armed:
                log_event("vehicle", "armed" if armed else "disarmed",
                          level="WARN" if armed else "INFO", mode=mode)
            self._maybe_log()
            return

        if msg_type == "STATUSTEXT":
            # ArduPilot's own words: prearm failures, failsafe announcements,
            # errors. Dropping these blinds the operator (and cost us a live
            # debugging session once) — ring-buffer + ops log, like the
            # messages panel in Mission Planner.
            text = msg.text
            if isinstance(text, bytes):
                text = text.decode(errors="ignore")
            text = text.rstrip("\x00").strip()
            sev = getattr(msg, "severity", 6)
            sev_name = _MAV_SEVERITY[sev] if 0 <= sev < len(_MAV_SEVERITY) else str(sev)
            with self._telem_lock:
                self._statustext.append(
                    {"t": round(time.time(), 2), "severity": sev, "severity_name": sev_name,
                     "text": text})
            log_event("vehicle", "statustext",
                      level=("ERROR" if sev <= 3 else "WARN" if sev == 4 else "INFO"),
                      severity=sev_name, text=text)
            return

        if msg_type == "AUTOPILOT_VERSION":
            # Protocol capabilities + firmware version, fetched once on connect.
            # Lets us verify MISSION_INT/COMMAND_INT support instead of assuming.
            caps = int(getattr(msg, "capabilities", 0))
            v = int(getattr(msg, "flight_sw_version", 0))
            self._capabilities = {
                name: bool(caps & bit) for name, bit in _CAP_BITS.items()
            }
            self._capabilities.update({
                "raw": caps,
                # flight_sw_version packs major<<24 | minor<<16 | patch<<8 | type.
                "fw_version": f"{(v >> 24) & 0xFF}.{(v >> 16) & 0xFF}.{(v >> 8) & 0xFF}",
                "vendor_id": int(getattr(msg, "vendor_id", 0)),
                "product_id": int(getattr(msg, "product_id", 0)),
            })
            log_event("link", "capabilities", fw_version=self._capabilities["fw_version"],
                      mission_int=self._capabilities.get("mission_int"),
                      command_int=self._capabilities.get("command_int"))
            return

        with self._telem_lock:
            if msg_type == "GLOBAL_POSITION_INT":
                self.telemetry.lat = msg.lat / 1e7
                self.telemetry.lon = msg.lon / 1e7
                # relative_alt is height above home/launch -- what a pilot wants.
                # (VFR_HUD.alt is AMSL, so we deliberately don't use it for altitude.)
                self.telemetry.altitude = msg.relative_alt / 1000.0
                self.telemetry.heading = msg.hdg // 100

            elif msg_type == "VFR_HUD":
                self.telemetry.airspeed = msg.airspeed
                self.telemetry.groundspeed = msg.groundspeed
                self.telemetry.heading = msg.heading

            elif msg_type == "ATTITUDE":
                self.telemetry.pitch = msg.pitch
                self.telemetry.roll = msg.roll
                self.telemetry.yaw = msg.yaw

            elif msg_type == "SYS_STATUS":
                self.telemetry.battery_voltage = msg.voltage_battery / 1000.0
                self.telemetry.battery_current = msg.current_battery / 100.0
                self.telemetry.battery_level = msg.battery_remaining
                # A sensor that is enabled but not reporting healthy is a
                # prearm-grade problem — surface it by name.
                enabled = getattr(msg, "onboard_control_sensors_enabled", 0)
                health = getattr(msg, "onboard_control_sensors_health", 0)
                errors = [name for name, bit in _SENSOR_BITS.items()
                          if enabled & bit and not (health & bit)]
                if errors != self.telemetry.sensor_errors:
                    log_event("vehicle", "sensor_health", level="WARN" if errors else "INFO",
                              unhealthy=errors)
                self.telemetry.sensor_errors = errors

            elif msg_type == "BATTERY_STATUS":
                # Richer battery data than SYS_STATUS: charge consumed this flight
                # and a (often better) remaining estimate. -1 means "unknown".
                consumed = getattr(msg, "current_consumed", -1)
                if consumed is not None and consumed >= 0:
                    self.telemetry.battery_consumed_mah = float(consumed)
                rem = getattr(msg, "battery_remaining", -1)
                if rem is not None and rem >= 0:
                    self.telemetry.battery_level = rem

            elif msg_type == "EKF_STATUS_REPORT":
                flags = msg.flags
                self.telemetry.ekf_flags = flags
                self.telemetry.ekf_healthy = bool(
                    flags & _EKF_ATTITUDE
                    and flags & (_EKF_POS_HORIZ_REL | _EKF_POS_HORIZ_ABS)
                    and not flags & _EKF_CONST_POS_MODE
                    and not flags & _EKF_UNINITIALIZED)

            elif msg_type == "GPS_RAW_INT":
                self.telemetry.gps_fix = msg.fix_type
                self.telemetry.gps_satellites = msg.satellites_visible

            elif msg_type in ("RC_CHANNELS", "RC_CHANNELS_RAW"):
                count = getattr(msg, "chancount", 8) or 8
                chans = []
                for i in range(1, min(count, 16) + 1):
                    v = getattr(msg, f"chan{i}_raw", 0)
                    # 65535 = "not present" in the MAVLink spec; treat as 0.
                    chans.append(0 if v in (65535, None) else v)
                self.telemetry.rc_channels = chans
                rssi = getattr(msg, "rssi", 0)
                self.telemetry.rc_rssi = 0 if rssi in (255, None) else rssi

            elif msg_type == "SERVO_OUTPUT_RAW":
                self.telemetry.servo_outputs = [
                    getattr(msg, f"servo{i}_raw", 0) or 0 for i in range(1, 9)
                ]

            elif msg_type == "MISSION_CURRENT":
                # Note: we deliberately ignore msg.total — its semantics vary
                # across firmware versions. mission_count comes from our own
                # upload/download, which we know is home-exclusive.
                self.telemetry.mission_seq = msg.seq

            elif msg_type == "NAV_CONTROLLER_OUTPUT":
                self.telemetry.wp_dist = float(msg.wp_dist)

            elif msg_type == "HOME_POSITION":
                self.home_lat = msg.latitude / 1e7
                self.home_lon = msg.longitude / 1e7
                self.home_alt = msg.altitude / 1000.0
                self.telemetry.home_lat = self.home_lat
                self.telemetry.home_lon = self.home_lon

    def snapshot(self) -> dict:
        """Thread-safe copy of the vehicle state plus link-health fields.
        This is the single source the API serves — routers must not read
        .telemetry directly (torn multi-field reads across threads)."""
        with self._telem_lock:
            d = asdict(self.telemetry)
            d["statustext"] = list(self._statustext)[-5:]
        d["connected"] = self.connected
        d["reconnecting"] = self.reconnecting
        now = time.time()
        d["last_heartbeat_age"] = (round(now - self._last_heartbeat, 2)
                                   if self.connected and self._last_heartbeat else None)
        # M2: connection state machine + link identity + capabilities.
        d["link_state"] = self._link_state
        d["link_level"] = self._link_level(d["last_heartbeat_age"])
        d["gcs_sysid"] = self._sysid
        d["vehicle_sysid"] = self._vehicle_sysid
        d["capabilities"] = self._capabilities
        d["msgs_filtered"] = self._msgs_filtered
        # M4: surfaced so the harness/UI can tell an injected GCS-loss from a
        # real one.
        d["gcs_hb_suppressed"] = self._suppress_gcs_hb
        # Guardian verdicts ride along so the UI gets them over the WS free.
        with self._guardian_lock:
            d["guardian"] = dict(self._guardian)
        with self._param_lock:
            d["param_sync"] = dict(self._param_sync)
        # Link quality: fraction of expected 1Hz vehicle heartbeats actually
        # received over the last 10 s (window shrinks right after connect so a
        # fresh link isn't reported as degraded).
        if self.connected and self._connected_at:
            window = min(10.0, max(1.0, now - self._connected_at))
            got = sum(1 for t in self._hb_times if now - t <= window)
            d["link_quality"] = min(100, int(round(100.0 * got / window)))
        else:
            d["link_quality"] = None
        return d

    def _on_link_lost(self):
        """Called when no heartbeat has arrived within the timeout. Mark the
        vehicle disconnected and tear the link down so the UI reflects it and a
        clean reconnect is possible."""
        log_event("link", "link_lost", level="ERROR",
                  last_heartbeat_age=round(time.time() - self._last_heartbeat, 2),
                  will_reconnect=bool(self._auto_reconnect and self._conn_params))
        self._set_state(LinkState.LOST, level="ERROR")
        self._running = False
        self.connected = False
        # Drop the vehicle identity lock — a reconnect re-acquires it from the
        # next heartbeat (which may be a different vehicle on the same string).
        self._vehicle_sysid = None
        self._vehicle_compid = None
        self._close_log()
        try:
            if self.connection:
                self.connection.close()
        except Exception:
            pass
        self.connection = None
        # Unexpected loss (not operator-initiated): try to get the link back.
        if self._auto_reconnect and self._conn_params:
            threading.Thread(target=self._reconnect_loop, daemon=True).start()

    def _reconnect_loop(self, max_attempts: int = 20, delay_s: float = 5.0):
        """Retry the last connection after a link drop (USB reseated, radio
        back in range). Gives up after max_attempts; a user disconnect()
        aborts it immediately via the _auto_reconnect flag."""
        self.reconnecting = True
        log_event("link", "reconnecting", level="WARN",
                  max_attempts=max_attempts, delay_s=delay_s)
        try:
            for _ in range(max_attempts):
                if not self._auto_reconnect or self.connected:
                    return
                cs, baud = self._conn_params
                try:
                    self.connect(cs, baud)
                    return  # success — telemetry loop is running again
                except Exception:
                    time.sleep(delay_s)
            log_event("link", "reconnect_gave_up", level="ERROR", attempts=max_attempts)
        finally:
            self.reconnecting = False

    # --- Flight logging: one JSON-lines file per flight (arm -> disarm) ---
    def _maybe_log(self):
        """Called on each heartbeat. Opens a log when the vehicle arms, samples
        telemetry at ~4 Hz while armed, and closes the log when it disarms."""
        armed = self.telemetry.armed
        if armed and self._log_fh is None:
            self._open_log()
        elif not armed and self._log_fh is not None:
            self._close_log()

        if self._log_fh is None:
            return
        now = time.time()
        if now - self._last_sample < 0.25:
            return
        self._last_sample = now
        t = self.telemetry
        rec = {
            "t": round(now - self._log_start, 2),
            "lat": t.lat, "lon": t.lon, "alt": round(t.altitude, 2),
            "hdg": t.heading, "as": round(t.airspeed, 2), "gs": round(t.groundspeed, 2),
            "pitch": round(t.pitch, 4), "roll": round(t.roll, 4), "yaw": round(t.yaw, 4),
            "volt": t.battery_voltage, "batt": t.battery_level, "mode": t.mode,
        }
        try:
            self._log_fh.write(json.dumps(rec) + "\n")
            self._log_fh.flush()
        except Exception:
            pass

    def _open_log(self):
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            name = "flight_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".jsonl"
            self._log_fh = open(LOG_DIR / name, "w", encoding="utf-8")
            self._log_start = time.time()
            self._last_sample = 0.0
            # Header line carries flight metadata (marked with "meta").
            self._log_fh.write(json.dumps({
                "meta": True,
                "started": datetime.now().isoformat(timespec="seconds"),
                "home_lat": self.home_lat, "home_lon": self.home_lon,
            }) + "\n")
        except Exception:
            self._log_fh = None

    def _close_log(self):
        if self._log_fh is not None:
            try:
                self._log_fh.close()
            except Exception:
                pass
            self._log_fh = None

    def set_mode(self, mode: str) -> bool:
        """Command a flight-mode change and wait for the vehicle's COMMAND_ACK.
        Returns True only if the vehicle ACCEPTED the change — a mode the
        vehicle doesn't know or refuses (e.g. mode-change denied) returns
        False so callers never report a mode switch that didn't happen."""
        conn = self.connection
        if not conn:
            return False
        mapping = conn.mode_mapping() or {}
        mode_id = mapping.get(mode)
        if mode_id is None:
            log_event("command", "set_mode", level="WARN", mode=mode, ok=False,
                      error="unknown mode for this vehicle")
            return False
        # Same wire message mavutil.set_mode sends (COMMAND_LONG DO_SET_MODE),
        # but we hold the link lock so the telemetry thread can't steal the ack.
        with self._link_lock:
            with self._send_lock:
                conn.mav.command_long_send(
                    conn.target_system, conn.target_component,
                    mavutil.mavlink.MAV_CMD_DO_SET_MODE, 0,
                    mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, mode_id,
                    0, 0, 0, 0, 0)
            ack = self._wait_command_ack(mavutil.mavlink.MAV_CMD_DO_SET_MODE)
        ok = ack is not None and ack.result == mavutil.mavlink.MAV_RESULT_ACCEPTED
        log_event("command", "set_mode", level="INFO" if ok else "WARN",
                  mode=mode, ok=ok, ack_result=(ack.result if ack else None))
        return ok

    def _wait_command_ack(self, command: int, timeout: float = 5.0):
        """Read COMMAND_ACK for a specific command. Must be called while holding
        _link_lock, otherwise the telemetry thread will consume (and drop) the ack."""
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            ack = self._recv_blocking(self.connection, "COMMAND_ACK", remaining)
            if ack is None:
                return None
            if ack.command == command:
                return ack

    def arm(self, force: bool = False) -> dict:
        """Arm the vehicle. `force` bypasses pre-arm safety checks (21196 magic).
        Returns whether the vehicle actually accepted the command."""
        if not self.connection:
            return {"ok": False, "error": "not connected"}
        conn = self.connection
        with self._link_lock:
            with self._send_lock:
                conn.mav.command_long_send(
                    conn.target_system, conn.target_component,
                    mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0,
                    1, (21196 if force else 0), 0, 0, 0, 0, 0)
            ack = self._wait_command_ack(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM)
        if ack is None:
            log_event("command", "arm", level="ERROR", force=force, ok=False,
                      error="no acknowledgement from vehicle")
            return {"ok": False, "error": "no acknowledgement from vehicle"}
        ok = ack.result == mavutil.mavlink.MAV_RESULT_ACCEPTED
        log_event("command", "arm", level="INFO" if ok else "WARN",
                  force=force, ok=ok, ack_result=ack.result)
        return {"ok": ok, "result": ack.result,
                "error": None if ok else "vehicle rejected arming (pre-arm checks?)"}

    def disarm(self, force: bool = False) -> dict:
        if not self.connection:
            return {"ok": False, "error": "not connected"}
        conn = self.connection
        with self._link_lock:
            with self._send_lock:
                conn.mav.command_long_send(
                    conn.target_system, conn.target_component,
                    mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0,
                    0, (21196 if force else 0), 0, 0, 0, 0, 0)
            ack = self._wait_command_ack(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM)
        ok = ack is not None and ack.result == mavutil.mavlink.MAV_RESULT_ACCEPTED
        log_event("command", "disarm", level="INFO" if ok else "WARN",
                  force=force, ok=ok, ack_result=(ack.result if ack else None))
        if ok:
            return {"ok": True, "result": ack.result, "error": None}
        return {"ok": False,
                "result": (ack.result if ack else None),
                "error": ("no acknowledgement from vehicle" if ack is None
                          else "vehicle rejected disarm (in flight? use force)")}

    def takeoff(self, alt: float = 100.0, force: bool = False) -> dict:
        """Auto-takeoff for a fixed-wing: load a minimal takeoff+loiter mission at
        the current position, switch to AUTO, and arm. The plane launches itself
        and climbs to `alt`. Each step manages its own link lock, so we must not
        hold the lock here (threading.Lock is not reentrant)."""
        if not self.connection:
            return {"ok": False, "error": "not connected"}
        if self.telemetry.armed:
            return {"ok": False, "error": "already armed / airborne"}
        if self.telemetry.gps_fix < 3:
            return {"ok": False, "error": "waiting for GPS 3D fix"}
        # The composing steps (upload/set_mode/arm) each log their own result.
        log_event("command", "takeoff_requested", alt=alt, force=force)

        lat, lon = self.telemetry.lat, self.telemetry.lon
        mission = [
            {"command": "TAKEOFF", "lat": lat, "lon": lon, "alt": alt},
            {"command": "LOITER", "lat": lat, "lon": lon, "alt": alt},
        ]
        up = self.upload_mission(mission)
        if not up.get("ok"):
            return {"ok": False, "error": f"could not load takeoff mission: {up.get('error')}"}

        if not self.set_mode("AUTO"):
            return {"ok": False, "error": "vehicle refused AUTO mode"}
        time.sleep(0.5)
        armed = self.arm(force=force)
        if not armed.get("ok"):
            return {"ok": False, "error": armed.get("error", "arming failed"),
                    "hint": "enable Force arm to bypass pre-arm checks"}
        return {"ok": True, "alt": alt}

    def land(self) -> dict:
        """Auto-land a fixed-wing at the home/launch point: load an approach
        waypoint + NAV_LAND touchdown and fly it in AUTO. The plane descends
        along the line from the approach fix to the landing point and touches
        down (ArduPlane disarms itself after landing)."""
        if not self.connection:
            return {"ok": False, "error": "not connected"}
        home_lat = self.home_lat or self.telemetry.lat
        home_lon = self.home_lon or self.telemetry.lon
        if not home_lat or not home_lon:
            return {"ok": False, "error": "home/position not known yet"}
        log_event("command", "land_requested", home_lat=home_lat, home_lon=home_lon)

        # Approach fix ~600 m north of home at 80 m; the plane descends along the
        # line from this fix down to the touchdown point.
        approach_lat = home_lat + 600.0 / 111320.0
        mission = [
            {"command": "WAYPOINT", "lat": approach_lat, "lon": home_lon, "alt": 80.0},
            {"command": "LAND", "lat": home_lat, "lon": home_lon, "alt": 0.0},
        ]
        up = self.upload_mission(mission)
        if not up.get("ok"):
            return {"ok": False, "error": f"could not load landing mission: {up.get('error')}"}

        # Start from the approach leg (seq 1) rather than continuing an old index.
        with self._send_lock:
            self.connection.mav.mission_set_current_send(
                self.connection.target_system, self.connection.target_component, 1)
        if not self.set_mode("AUTO"):
            return {"ok": False, "error": "vehicle refused AUTO mode"}
        # Tell the guardian's state machine this AUTO is a landing sequence.
        self._landing_requested = True
        return {"ok": True}

    def get_available_modes(self) -> list[str]:
        # NOTE: no "LAND" here — ArduPlane has no LAND mode (only quadplanes
        # have QLAND); advertising it made the UI offer a mode the vehicle
        # silently rejected. Landing is done via the /land mission flow.
        return [
            "MANUAL", "STABILIZE", "FBWA", "FBWB", "AUTO",
            "RTL", "LOITER", "GUIDED", "CIRCLE"
        ]

    def get_param(self, name: str):
        conn = self.connection
        if not conn:
            return None
        with self._link_lock:
            with self._send_lock:
                conn.param_fetch_one(name)
            msg = self._recv_blocking(conn, "PARAM_VALUE", 5)
        return msg.param_value if msg else None

    # Seconds to wait for a PARAM_VALUE (the type read, the set echo, and the
    # read-back fallback). ArduPilot echoes a PARAM_SET almost immediately, so
    # 1s is generous even on a lossy telemetry radio. Class attr so tests can
    # shrink it. Bounds the worst case (unknown/rejected param) to
    #   _PARAM_TIMEOUT * (1 + 2*_PARAM_SET_ATTEMPTS)  ≈ 5s.
    _PARAM_TIMEOUT = 1.0
    # How many times set-then-verify is attempted before giving up (initial try
    # plus retries) — covers a dropped PARAM_SET or echo on a lossy radio.
    _PARAM_SET_ATTEMPTS = 2

    # MAV_PARAM_TYPE codes that store an integer (value transported as a float
    # per ArduPilot's cast convention, but must compare as ints, not floats).
    _INT_PARAM_TYPES = frozenset({
        mavutil.mavlink.MAV_PARAM_TYPE_UINT8, mavutil.mavlink.MAV_PARAM_TYPE_INT8,
        mavutil.mavlink.MAV_PARAM_TYPE_UINT16, mavutil.mavlink.MAV_PARAM_TYPE_INT16,
        mavutil.mavlink.MAV_PARAM_TYPE_UINT32, mavutil.mavlink.MAV_PARAM_TYPE_INT32,
        mavutil.mavlink.MAV_PARAM_TYPE_UINT64, mavutil.mavlink.MAV_PARAM_TYPE_INT64,
    })

    @staticmethod
    def _param_name(msg) -> str:
        """Decode a PARAM_VALUE's param_id (bytes or str, NUL-padded)."""
        pid = msg.param_id
        if isinstance(pid, bytes):
            pid = pid.decode(errors="ignore")
        return pid.rstrip("\x00")

    @classmethod
    def _param_close(cls, accepted, requested: float, param_type: int) -> bool:
        """Did the vehicle store the value we asked for? Integer params must
        match exactly (after rounding — they ride the wire as float32); float
        params only need to match within float32 precision, since the FC
        round-trips a float64 request through a 32-bit store."""
        if accepted is None:
            return False
        if param_type in cls._INT_PARAM_TYPES:
            return round(accepted) == round(requested)
        return abs(accepted - requested) <= max(1e-4, abs(requested) * 1e-4)

    def _match_param_value(self, conn, name: str, deadline: float):
        """Wait (without sending anything) for a PARAM_VALUE whose id is `name`,
        skipping unrelated PARAM_VALUEs, until `deadline`. Returns the msg or None.
        Must be called holding _link_lock."""
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            msg = self._recv_blocking(conn, "PARAM_VALUE", remaining)
            if msg is None:
                return None
            if self._param_name(msg) == name:
                return msg

    def _request_param(self, conn, name: str, timeout: float = None):
        """Request one parameter and return its PARAM_VALUE msg (value + type),
        matched by id. Must be called holding _link_lock."""
        if timeout is None:
            timeout = self._PARAM_TIMEOUT
        with self._send_lock:
            conn.mav.param_request_read_send(
                conn.target_system, conn.target_component, name.encode(), -1)
        return self._match_param_value(conn, name, time.time() + timeout)

    def _set_param_locked(self, conn, name: str, value: float, attempts: int = None) -> dict:
        """Write one parameter and confirm the vehicle accepted it. Assumes
        _link_lock is held (so the telemetry thread can't steal the echo).

        M1b flow: read the param's real storage type -> PARAM_SET with that type
        -> wait for the PARAM_VALUE echo the FC sends back -> compare. `verified`
        is True only when the echoed value matches what we asked for; the caller
        (and the operator) get the *actual accepted value*, not an assumption."""
        if attempts is None:
            attempts = self._PARAM_SET_ATTEMPTS
        value = float(value)
        # 1. Learn the real MAV_PARAM_TYPE (and previous value). ArduPilot uses
        #    the cast convention, so sending the correct type is what keeps this
        #    honest for INTx params and any future PX4-convention airframe.
        cur = self._request_param(conn, name)
        if cur is not None:
            param_type = int(cur.param_type)
            previous = cur.param_value
            self._cache_put(name, cur.param_value, param_type,
                            getattr(cur, "param_index", None))
        else:
            # Fall back to the cached type if the vehicle didn't answer the read.
            param_type = self.cached_type(name) or mavutil.mavlink.MAV_PARAM_TYPE_REAL32
            previous = self.cached_value(name)

        accepted = None
        for _ in range(max(1, attempts)):
            with self._send_lock:
                conn.mav.param_set_send(
                    conn.target_system, conn.target_component,
                    name.encode(), value, param_type)
            # ArduPilot echoes a PARAM_VALUE for every PARAM_SET it applies.
            echo = self._match_param_value(conn, name, time.time() + self._PARAM_TIMEOUT)
            if echo is None:
                # Echo dropped (lossy radio) — read the stored value back instead.
                echo = self._request_param(conn, name)
            if echo is not None:
                accepted = echo.param_value
                if self._param_close(accepted, value, param_type):
                    break

        verified = self._param_close(accepted, value, param_type)
        if accepted is not None:
            # Keep the cache truthful with whatever the FC actually stored.
            self._cache_put(name, accepted, param_type)
        result = {
            "ok": verified,
            "name": name,
            "requested": value,
            "accepted": accepted,
            "previous": previous,
            "param_type": param_type,
            "verified": verified,
        }
        if not verified:
            result["error"] = (
                "no PARAM_VALUE echo from vehicle" if accepted is None
                else f"vehicle stored {accepted}, not the requested {value}")
        return result

    # --- M3: parameter cache & validation ---
    def _cache_put(self, name, value, ptype=None, index=None):
        """Insert/update one param in the cache, preserving type/index when a
        later update (e.g. a set echo) doesn't carry them."""
        with self._param_lock:
            entry = self._param_cache.get(name, {})
            entry["value"] = value
            if ptype is not None:
                entry["type"] = int(ptype)
            if index is not None and index >= 0:
                entry["index"] = int(index)
            self._param_cache[name] = entry

    def cached_type(self, name):
        with self._param_lock:
            e = self._param_cache.get(name)
            return e.get("type") if e else None

    def cached_value(self, name):
        with self._param_lock:
            e = self._param_cache.get(name)
            return e.get("value") if e else None

    def get_cached_params(self) -> dict:
        """The parameter cache as {name: value} — served without touching the
        link. Empty until the on-connect sync has run."""
        with self._param_lock:
            return {k: v.get("value") for k, v in self._param_cache.items()}

    def _validate_write(self, name, value):
        """(ok, error) for a proposed write, using the cached MAV_PARAM_TYPE when
        we have it so int/range rules apply. Keeps invalid values off the wire."""
        return param_meta.validate(name, value, self.cached_type(name))

    def set_param(self, name: str, value: float) -> dict:
        """Write a single parameter with metadata validation + echo verification.
        Returns a result dict whose `verified`/`accepted` fields report what the
        FC actually did — never a blind success."""
        ok, err = self._validate_write(name, value)
        if not ok:
            log_event("param", "set_rejected", level="WARN", name=name,
                      requested=value, error=err)
            return {"ok": False, "name": name, "requested": float(value),
                    "accepted": None, "verified": False, "rejected": True, "error": err}
        if not self.connection:
            return {"ok": False, "name": name, "requested": float(value),
                    "accepted": None, "verified": False, "error": "not connected"}
        conn = self.connection
        with self._link_lock:
            result = self._set_param_locked(conn, name, value)
        log_event("param", "set", name=result["name"], requested=result["requested"],
                  accepted=result["accepted"], verified=result["verified"],
                  previous=result.get("previous"), error=result.get("error"))
        return result

    def _absorb_param_value(self, msg):
        """Record one PARAM_VALUE into the cache and return (name, index)."""
        pid = msg.param_id
        if isinstance(pid, bytes):
            pid = pid.decode(errors="ignore")
        name = pid.rstrip("\x00")
        idx = getattr(msg, "param_index", None)
        self._cache_put(name, msg.param_value, msg.param_type, idx)
        return name, idx

    def get_all_params(self, timeout: float = 25.0) -> dict:
        """Full parameter sync into the cache, then return {name: value}.

        Requests the whole table, then RE-REQUESTS any indices the stream missed
        (a dropped PARAM_VALUE on a lossy radio) individually by index — so the
        cache ends up complete instead of silently short a few params. Holds the
        link lock (telemetry pauses briefly); PARAM_VALUE traffic keeps both
        watchdogs fed."""
        if not self.connection:
            return {}
        conn = self.connection
        got = {}          # index -> name, for missing-index detection
        total = None
        with self._param_lock:
            self._param_sync = {"syncing": True, "received": 0, "total": None,
                                "synced": False}
        try:
            return self._get_all_params_locked(conn, got, timeout)
        except Exception as e:
            # Found live by the M4 link-watchdog scenario: the vehicle dying
            # mid-sync (socket reset) killed this thread with the progress
            # stuck at syncing=True forever. A failed sync must end visibly.
            with self._param_lock:
                self._param_sync = {"syncing": False,
                                    "received": len(self._param_cache),
                                    "total": total, "synced": False}
            log_event("param", "sync_failed", level="WARN", error=str(e))
            return self.get_cached_params()

    def _get_all_params_locked(self, conn, got: dict, timeout: float) -> dict:
        total = None
        with self._link_lock:
            with self._send_lock:
                conn.mav.param_request_list_send(conn.target_system, conn.target_component)
            start = time.time()
            last_rx = start
            while time.time() - start < timeout:
                msg = self._recv_blocking(conn, "PARAM_VALUE", 2)
                if msg is None:
                    if time.time() - last_rx > 4:
                        break  # stream dried up — fall through to gap-fill
                    continue
                last_rx = time.time()
                self._last_heartbeat = last_rx  # param traffic = link alive
                name, idx = self._absorb_param_value(msg)
                total = msg.param_count or total
                # Track only real table indices. ArduPilot sends param_index=65535
                # (the "not part of the list" sentinel) on echoes/individual reads
                # — those must not inflate the received count past total.
                if idx is not None and 0 <= idx < (total or idx + 1):
                    got[idx] = name
                with self._param_lock:
                    self._param_sync.update(received=len(got) or len(self._param_cache),
                                            total=total)
                if total and len(got) >= total:
                    break

            # Gap-fill: re-request any indices [0, total) we never received.
            if total:
                for _round in range(3):
                    missing = [i for i in range(total) if i not in got]
                    if not missing:
                        break
                    log_event("param", "sync_gapfill", level="INFO",
                              missing=len(missing), round=_round)
                    for i in missing:
                        with self._send_lock:
                            conn.mav.param_request_read_send(
                                conn.target_system, conn.target_component, b"", i)
                        msg = self._recv_blocking(conn, "PARAM_VALUE", 1.5)
                        if msg is None:
                            continue
                        self._last_heartbeat = time.time()
                        name, idx = self._absorb_param_value(msg)
                        if idx is not None and 0 <= idx < total:
                            got[idx] = name

        self._param_count = total
        with self._param_lock:
            n = len(self._param_cache)
            self._param_sync = {"syncing": False, "received": n, "total": total,
                                "synced": bool(total and len(got) >= total)}
        log_event("param", "sync_complete", received=len(got), total=total,
                  cached=n)
        return self.get_cached_params()

    def sync_params(self):
        """Full param sync in a background thread (used on connect). Safe to call
        again; a sync already in flight is left to finish."""
        t = self._param_sync_thread
        if t is not None and t.is_alive():
            return
        self._param_sync_thread = threading.Thread(
            target=self.get_all_params, daemon=True)
        self._param_sync_thread.start()

    def get_params(self, names: list[str]) -> dict:
        """Fetch several named parameters, matching each reply by param_id so we
        never mismatch values. Held under the link lock (it reads the link)."""
        if not self.connection:
            return {}
        conn = self.connection
        result = {}
        with self._link_lock:
            for name in names:
                with self._send_lock:
                    conn.mav.param_request_read_send(
                        conn.target_system, conn.target_component, name.encode(), -1)
                start = time.time()
                while time.time() - start < 1.5:
                    msg = self._recv_blocking(conn, "PARAM_VALUE", 1.5)
                    if msg is None:
                        break
                    pid = msg.param_id
                    if isinstance(pid, bytes):
                        pid = pid.decode(errors="ignore")
                    if pid.rstrip("\x00") == name:
                        result[name] = msg.param_value
                        break
        return result

    def set_params(self, params: dict) -> dict:
        """Set several parameters, each validated + echo-verified against the
        vehicle. One link-lock transaction for the whole batch. Returns per-param
        results and an overall `ok` that is True only when every write was
        confirmed — so SafetyPanel can no longer claim a fence/failsafe took when
        it didn't."""
        if not self.connection:
            return {"ok": False, "error": "not connected", "results": {}}
        # Validate everything up front; a value that fails is reported without
        # being sent (the others still go).
        conn = self.connection
        results = {}
        with self._link_lock:
            for name, value in params.items():
                ok, err = self._validate_write(name, value)
                if not ok:
                    results[name] = {"ok": False, "name": name,
                                     "requested": float(value), "accepted": None,
                                     "verified": False, "rejected": True, "error": err}
                    continue
                results[name] = self._set_param_locked(conn, name, value)
        failed = [n for n, r in results.items() if not r["verified"]]
        log_event("param", "set_batch",
                  requested={k: float(v) for k, v in params.items()},
                  accepted={n: r["accepted"] for n, r in results.items()},
                  failed=failed)
        return {
            "ok": not failed,
            "count": len(params),
            "verified": len(params) - len(failed),
            "failed": failed,
            "results": results,
        }

    def set_params_atomic(self, params: dict) -> dict:
        """All-or-nothing batch set. Validate everything first; if any value is
        invalid, send nothing. Otherwise apply in order, and if any write fails
        verification, ROLL BACK the ones that already took to their previous
        values — so a half-applied fence/failsafe never survives. One link-lock
        transaction end to end."""
        if not self.connection:
            return {"ok": False, "error": "not connected", "results": {}}
        # Pre-flight validation: reject the whole batch if anything is invalid,
        # before a single PARAM_SET goes out.
        for name, value in params.items():
            ok, err = self._validate_write(name, value)
            if not ok:
                log_event("param", "atomic_rejected", level="WARN", name=name, error=err)
                return {"ok": False, "error": f"{name}: {err}", "rejected": name,
                        "results": {}}

        conn = self.connection
        results, applied = {}, []
        with self._link_lock:
            for name, value in params.items():
                r = self._set_param_locked(conn, name, value)
                results[name] = r
                if r["verified"]:
                    applied.append((name, r.get("previous")))
                else:
                    # One failed — undo the ones already applied (reverse order).
                    rolled = self._rollback_locked(conn, applied)
                    log_event("param", "atomic_rollback", level="ERROR",
                              failed=name, error=r.get("error"),
                              rolled_back=[n for n, _ in applied], rollback_ok=rolled)
                    return {"ok": False, "failed": name, "error": r.get("error"),
                            "rolled_back": [n for n, _ in applied],
                            "rollback_ok": rolled, "results": results}
        log_event("param", "atomic_ok", params=list(params))
        return {"ok": True, "count": len(params), "results": results}

    def _rollback_locked(self, conn, applied):
        """Restore params to their captured previous values (link lock held).
        Returns True only if every restore verified. A previous value of None
        (param wasn't in cache and the FC didn't echo a prior) can't be restored
        and counts as a failed rollback."""
        ok = True
        for name, prev in reversed(applied):
            if prev is None:
                ok = False
                continue
            r = self._set_param_locked(conn, name, prev)
            ok = ok and r["verified"]
        return ok

    def send_rc_override(self, channels: list) -> bool:
        """Push RC channel values (PWM µs) into the vehicle as RC_CHANNELS_OVERRIDE
        — used to fly from a laptop-connected transmitter/gamepad. 0 on a channel
        releases it back to the real RC input. Must be sent continuously (~20Hz)
        or ArduPilot times the override out."""
        # Capture locally: the telemetry thread can null self.connection at any
        # moment (link loss) and this is called from other threads.
        conn = self.connection
        if not conn:
            return False
        # Reject malformed frames outright (e.g. a dict) instead of raising —
        # a single bad frame must never take down the manual-control stream.
        if not isinstance(channels, (list, tuple)):
            return False
        ch = []
        for i in range(8):
            v = channels[i] if i < len(channels) else 0
            try:
                v = int(v)
            except (TypeError, ValueError):
                v = 0
            ch.append(0 if v == 0 else max(900, min(2100, v)))
        try:
            with self._send_lock:
                conn.mav.rc_channels_override_send(
                    conn.target_system, conn.target_component, *ch)
        except Exception:
            # Port died mid-send (link loss race) — report failure, don't raise.
            return False
        return True

    def release_rc_override(self):
        """Release all RC overrides (send zeros) so the plane isn't left with the
        last stick positions after manual control stops."""
        conn = self.connection
        if not conn:
            return
        try:
            with self._send_lock:
                conn.mav.rc_channels_override_send(
                    conn.target_system, conn.target_component,
                    0, 0, 0, 0, 0, 0, 0, 0)
        except Exception:
            pass

    def upload_mission(self, items: list[dict]) -> dict:
        """Upload a mission. `items` is an ordered list of dicts with keys
        command (TAKEOFF/WAYPOINT/LOITER/LAND/RTL), lat, lon, alt, and optional
        param1. A home item is inserted automatically at seq 0."""
        if not self.connection:
            return {"ok": False, "error": "not connected"}
        conn = self.connection

        # Seq 0 must be the home location. Use the vehicle's current position
        # (fall back to the first item's location if we don't have a fix yet).
        home_lat = self.telemetry.lat or (items[0]["lat"] if items else 0.0)
        home_lon = self.telemetry.lon or (items[0]["lon"] if items else 0.0)
        full = [{"command": "WAYPOINT", "lat": home_lat, "lon": home_lon,
                 "alt": 0.0, "param1": 0.0}] + items

        def _send_item(seq: int):
            it = full[seq]
            cmd = _CMD_TO_MAV.get(it.get("command", "WAYPOINT"),
                                  mavutil.mavlink.MAV_CMD_NAV_WAYPOINT)
            with self._send_lock:
                conn.mav.mission_item_int_send(
                    conn.target_system, conn.target_component, seq,
                    mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT,
                    cmd, 0, 1,
                    # param1 = hold time / command-specific; param3 = loiter
                    # radius for LOITER items (ArduPlane convention).
                    float(it.get("param1", 0.0)), 0.0, float(it.get("radius", 0.0)), 0.0,
                    int(float(it["lat"]) * 1e7), int(float(it["lon"]) * 1e7), float(it["alt"]),
                )

        ack = None
        error = None
        with self._link_lock:
            with self._send_lock:
                conn.mav.mission_count_send(conn.target_system, conn.target_component, len(full))
            # The VEHICLE drives the transfer: it asks for each seq and — on a
            # lossy radio — re-asks for the same seq when a reply was lost, so
            # always answer with the exact seq requested (never a local
            # counter, which desyncs the whole transfer on one retransmit).
            # Bounded iterations so a misbehaving vehicle can't loop us forever.
            for _ in range(10 * len(full) + 20):
                msg = self._recv_blocking(
                    conn, ["MISSION_REQUEST", "MISSION_REQUEST_INT", "MISSION_ACK"], 5)
                if msg is None:
                    error = "mission transfer timed out (no request/ack from vehicle)"
                    break
                if msg.get_type() == "MISSION_ACK":
                    ack = msg
                    break
                if 0 <= msg.seq < len(full):
                    _send_item(msg.seq)
                # requests out of range: stale/corrupt frame — ignore
            else:
                error = "mission transfer did not complete (vehicle kept re-requesting)"
            if ack is None:
                # Close the half-open transaction so the vehicle doesn't sit
                # mid-transfer holding a partially-replaced mission.
                try:
                    with self._send_lock:
                        conn.mav.mission_ack_send(
                            conn.target_system, conn.target_component,
                            mavutil.mavlink.MAV_MISSION_OPERATION_CANCELLED)
                except Exception:
                    pass

        ok = ack is not None and ack.type == mavutil.mavlink.MAV_MISSION_ACCEPTED
        if ok:
            self.telemetry.mission_count = len(items)
        result = {"ok": ok, "ack": (ack.type if ack else None), "count": len(items)}
        if not ok:
            result["error"] = error or f"vehicle rejected mission (MISSION_ACK type {ack.type})"
        log_event("mission", "upload", level="INFO" if ok else "ERROR",
                  ok=ok, count=len(items), ack=result["ack"],
                  error=result.get("error"))
        return result

    def download_mission(self) -> list[dict]:
        """Download the mission from the vehicle. Returns the editable items
        (home / seq 0 is stripped out)."""
        if not self.connection:
            return []
        conn = self.connection
        result = []

        with self._link_lock:
            with self._send_lock:
                conn.mav.mission_request_list_send(conn.target_system, conn.target_component)
            count_msg = self._recv_blocking(conn, "MISSION_COUNT", 5)
            if not count_msg:
                return []
            for i in range(count_msg.count):
                with self._send_lock:
                    conn.mav.mission_request_int_send(conn.target_system, conn.target_component, i)
                item = self._recv_blocking(conn, ["MISSION_ITEM_INT", "MISSION_ITEM"], 5)
                if item is None:
                    continue
                cmd_name = _MAV_TO_CMD.get(item.command)
                if cmd_name is None:
                    continue  # skip command types we don't model in the editor
                if item.get_type() == "MISSION_ITEM_INT":
                    lat, lon = item.x / 1e7, item.y / 1e7
                else:
                    lat, lon = item.x, item.y
                result.append({"seq": item.seq, "command": cmd_name,
                               "lat": lat, "lon": lon, "alt": item.z})
            # Close the transaction so the vehicle stops expecting more requests.
            with self._send_lock:
                conn.mav.mission_ack_send(conn.target_system, conn.target_component,
                                          mavutil.mavlink.MAV_MISSION_ACCEPTED)

        items = [w for w in result if w["seq"] != 0]
        self.telemetry.mission_count = len(items)
        log_event("mission", "download", count=len(items))
        return items


# Singleton instance
vehicle_manager = VehicleManager()

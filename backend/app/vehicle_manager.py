import threading
import time
from dataclasses import dataclass
from typing import Optional
from pymavlink import mavutil

# Mission command types we support, mapped to their MAVLink NAV commands.
_CMD_TO_MAV = {
    "TAKEOFF": mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
    "WAYPOINT": mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
    "LOITER": mavutil.mavlink.MAV_CMD_NAV_LOITER_UNLIM,
    "LAND": mavutil.mavlink.MAV_CMD_NAV_LAND,
    "RTL": mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH,
}
_MAV_TO_CMD = {v: k for k, v in _CMD_TO_MAV.items()}


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
    pitch: float = 0.0
    roll: float = 0.0
    yaw: float = 0.0
    gps_fix: int = 0
    gps_satellites: int = 0


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
        # Serializes access to the MAVLink connection so the telemetry thread and
        # mission upload/download (which also read the link) don't steal each
        # other's messages. Held briefly per telemetry read, and for the whole
        # duration of a mission transaction.
        self._link_lock = threading.Lock()

    def connect(self, connection_string: str, baud: int = 57600) -> bool:
        try:
            self.connection = mavutil.mavlink_connection(connection_string, baud=baud)
            self.connection.wait_heartbeat(timeout=30)
            self.connected = True
            self.connection_string = connection_string
            self._last_heartbeat = time.time()
            self._mode_mapping = self.connection.mode_mapping()
            self._request_data_streams()
            # Ask for the home position (used by the landing flow).
            self.connection.mav.command_long_send(
                self.connection.target_system, self.connection.target_component,
                mavutil.mavlink.MAV_CMD_GET_HOME_POSITION, 0, 0, 0, 0, 0, 0, 0, 0)
            self._start_telemetry_loop()
            return True
        except Exception as e:
            self.connected = False
            raise ConnectionError(f"Failed to connect: {e}")

    def _request_data_streams(self, rate_hz: int = 10):
        """Ask the autopilot to stream telemetry. Without this, ArduPilot only
        sends heartbeats and we get no attitude/position/battery data."""
        self.connection.mav.request_data_stream_send(
            self.connection.target_system,
            self.connection.target_component,
            mavutil.mavlink.MAV_DATA_STREAM_ALL,
            rate_hz,
            1,  # start streaming
        )

    def disconnect(self):
        self._running = False
        if self._telemetry_thread:
            self._telemetry_thread.join(timeout=5)
        if self.connection:
            self.connection.close()
        self.connection = None
        self.connected = False
        self.connection_string = None

    def _start_telemetry_loop(self):
        self._running = True
        self._telemetry_thread = threading.Thread(target=self._update_telemetry, daemon=True)
        self._telemetry_thread.start()

    def _update_telemetry(self):
        while self._running and self.connection:
            # Link watchdog, checked every iteration up front so it fires whether
            # the read returns no data OR raises (a reset socket raises, which is
            # exactly what happens when the vehicle/SITL goes away).
            if time.time() - self._last_heartbeat > self._link_timeout:
                self._on_link_lost()
                break
            try:
                with self._link_lock:
                    msg = self.connection.recv_match(blocking=False)
                if msg is None:
                    time.sleep(0.01)
                    continue

                msg_type = msg.get_type()

                if msg_type == "HEARTBEAT":
                    self._last_heartbeat = time.time()
                    mode = mavutil.mode_string_v10(msg)
                    self.telemetry.mode = mode
                    self.telemetry.armed = (msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED) != 0

                elif msg_type == "GLOBAL_POSITION_INT":
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

                elif msg_type == "GPS_RAW_INT":
                    self.telemetry.gps_fix = msg.fix_type
                    self.telemetry.gps_satellites = msg.satellites_visible

                elif msg_type == "HOME_POSITION":
                    self.home_lat = msg.latitude / 1e7
                    self.home_lon = msg.longitude / 1e7
                    self.home_alt = msg.altitude / 1000.0

            except Exception:
                # Socket error / decode error: don't spin hot; the watchdog
                # below will mark us disconnected if heartbeats stop.
                time.sleep(0.05)

    def _on_link_lost(self):
        """Called when no heartbeat has arrived within the timeout. Mark the
        vehicle disconnected and tear the link down so the UI reflects it and a
        clean reconnect is possible."""
        self._running = False
        self.connected = False
        try:
            if self.connection:
                self.connection.close()
        except Exception:
            pass
        self.connection = None

    def set_mode(self, mode: str) -> bool:
        if not self.connection:
            return False
        mode_id = self.connection.mode_mapping().get(mode)
        if mode_id is None:
            return False
        self.connection.set_mode(mode_id)
        return True

    def _wait_command_ack(self, command: int, timeout: float = 5.0):
        """Read COMMAND_ACK for a specific command. Must be called while holding
        _link_lock, otherwise the telemetry thread will consume (and drop) the ack."""
        start = time.time()
        while time.time() - start < timeout:
            ack = self.connection.recv_match(type="COMMAND_ACK", blocking=True, timeout=timeout)
            if ack is None:
                return None
            if ack.command == command:
                return ack
        return None

    def arm(self, force: bool = False) -> dict:
        """Arm the vehicle. `force` bypasses pre-arm safety checks (21196 magic).
        Returns whether the vehicle actually accepted the command."""
        if not self.connection:
            return {"ok": False, "error": "not connected"}
        conn = self.connection
        with self._link_lock:
            conn.mav.command_long_send(
                conn.target_system, conn.target_component,
                mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0,
                1, (21196 if force else 0), 0, 0, 0, 0, 0)
            ack = self._wait_command_ack(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM)
        if ack is None:
            return {"ok": False, "error": "no acknowledgement from vehicle"}
        ok = ack.result == mavutil.mavlink.MAV_RESULT_ACCEPTED
        return {"ok": ok, "result": ack.result,
                "error": None if ok else "vehicle rejected arming (pre-arm checks?)"}

    def disarm(self, force: bool = False) -> dict:
        if not self.connection:
            return {"ok": False, "error": "not connected"}
        conn = self.connection
        with self._link_lock:
            conn.mav.command_long_send(
                conn.target_system, conn.target_component,
                mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0,
                0, (21196 if force else 0), 0, 0, 0, 0, 0)
            ack = self._wait_command_ack(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM)
        ok = ack is not None and ack.result == mavutil.mavlink.MAV_RESULT_ACCEPTED
        return {"ok": ok}

    def takeoff(self, alt: float = 100.0, force: bool = False) -> dict:
        """Auto-takeoff for a fixed-wing: load a minimal takeoff+loiter mission at
        the current position, switch to AUTO, and arm. The plane launches itself
        and climbs to `alt`. Each step manages its own link lock, so we must not
        hold the lock here (threading.Lock is not reentrant)."""
        if not self.connection:
            return {"ok": False, "error": "not connected"}
        if self.telemetry.gps_fix < 3:
            return {"ok": False, "error": "waiting for GPS 3D fix"}

        lat, lon = self.telemetry.lat, self.telemetry.lon
        mission = [
            {"command": "TAKEOFF", "lat": lat, "lon": lon, "alt": alt},
            {"command": "LOITER", "lat": lat, "lon": lon, "alt": alt},
        ]
        up = self.upload_mission(mission)
        if not up.get("ok"):
            return {"ok": False, "error": f"could not load takeoff mission: {up.get('error')}"}

        self.set_mode("AUTO")
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
        self.connection.mav.mission_set_current_send(
            self.connection.target_system, self.connection.target_component, 1)
        self.set_mode("AUTO")
        return {"ok": True}

    def get_available_modes(self) -> list[str]:
        return [
            "MANUAL", "STABILIZE", "FBWA", "FBWB", "AUTO",
            "RTL", "LOITER", "GUIDED", "CIRCLE", "LAND"
        ]

    def get_param(self, name: str):
        if not self.connection:
            return None
        with self._link_lock:
            self.connection.param_fetch_one(name)
            msg = self.connection.recv_match(type="PARAM_VALUE", blocking=True, timeout=5)
        return msg.param_value if msg else None

    def set_param(self, name: str, value: float) -> bool:
        if not self.connection:
            return False
        self.connection.param_set_send(name, value)
        return True

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

        with self._link_lock:
            conn.mav.mission_count_send(conn.target_system, conn.target_component, len(full))
            for i, it in enumerate(full):
                req = conn.recv_match(type=["MISSION_REQUEST", "MISSION_REQUEST_INT"],
                                      blocking=True, timeout=5)
                if req is None:
                    return {"ok": False, "error": f"no MISSION_REQUEST for seq {i}"}
                cmd = _CMD_TO_MAV.get(it.get("command", "WAYPOINT"),
                                      mavutil.mavlink.MAV_CMD_NAV_WAYPOINT)
                conn.mav.mission_item_int_send(
                    conn.target_system, conn.target_component, i,
                    mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT,
                    cmd, 0, 1,
                    float(it.get("param1", 0.0)), 0.0, 0.0, 0.0,
                    int(float(it["lat"]) * 1e7), int(float(it["lon"]) * 1e7), float(it["alt"]),
                )
            ack = conn.recv_match(type=["MISSION_ACK"], blocking=True, timeout=5)

        ok = ack is not None and ack.type == mavutil.mavlink.MAV_MISSION_ACCEPTED
        return {"ok": ok, "ack": (ack.type if ack else None), "count": len(items)}

    def download_mission(self) -> list[dict]:
        """Download the mission from the vehicle. Returns the editable items
        (home / seq 0 is stripped out)."""
        if not self.connection:
            return []
        conn = self.connection
        result = []

        with self._link_lock:
            conn.mav.mission_request_list_send(conn.target_system, conn.target_component)
            count_msg = conn.recv_match(type=["MISSION_COUNT"], blocking=True, timeout=5)
            if not count_msg:
                return []
            for i in range(count_msg.count):
                conn.mav.mission_request_int_send(conn.target_system, conn.target_component, i)
                item = conn.recv_match(type=["MISSION_ITEM_INT", "MISSION_ITEM"],
                                       blocking=True, timeout=5)
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
            conn.mav.mission_ack_send(conn.target_system, conn.target_component,
                                      mavutil.mavlink.MAV_MISSION_ACCEPTED)

        return [w for w in result if w["seq"] != 0]


# Singleton instance
vehicle_manager = VehicleManager()

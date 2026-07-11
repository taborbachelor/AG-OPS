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
            self._mode_mapping = self.connection.mode_mapping()
            self._request_data_streams()
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
            try:
                with self._link_lock:
                    msg = self.connection.recv_match(blocking=False)
                if msg is None:
                    time.sleep(0.01)
                    continue

                msg_type = msg.get_type()

                if msg_type == "HEARTBEAT":
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

            except Exception:
                pass

    def set_mode(self, mode: str) -> bool:
        if not self.connection:
            return False
        mode_id = self.connection.mode_mapping().get(mode)
        if mode_id is None:
            return False
        self.connection.set_mode(mode_id)
        return True

    def arm(self) -> bool:
        if not self.connection:
            return False
        self.connection.arducopter_arm()
        return True

    def disarm(self) -> bool:
        if not self.connection:
            return False
        self.connection.arducopter_disarm()
        return True

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

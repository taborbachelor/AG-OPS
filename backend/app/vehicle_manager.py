import threading
import time
from dataclasses import dataclass
from typing import Optional
from pymavlink import mavutil


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

    def connect(self, connection_string: str, baud: int = 57600) -> bool:
        try:
            self.connection = mavutil.mavlink_connection(connection_string, baud=baud)
            self.connection.wait_heartbeat(timeout=30)
            self.connected = True
            self.connection_string = connection_string
            self._mode_mapping = self.connection.mode_mapping()
            self._start_telemetry_loop()
            return True
        except Exception as e:
            self.connected = False
            raise ConnectionError(f"Failed to connect: {e}")

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
                    self.telemetry.altitude = msg.relative_alt / 1000.0
                    self.telemetry.heading = msg.hdg // 100

                elif msg_type == "VFR_HUD":
                    self.telemetry.airspeed = msg.airspeed
                    self.telemetry.groundspeed = msg.groundspeed
                    self.telemetry.heading = msg.heading
                    self.telemetry.altitude = msg.alt

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
        self.connection.param_fetch_one(name)
        msg = self.connection.recv_match(type="PARAM_VALUE", blocking=True, timeout=5)
        if msg:
            return msg.param_value
        return None

    def set_param(self, name: str, value: float) -> bool:
        if not self.connection:
            return False
        self.connection.param_set_send(name, value)
        return True

    def upload_mission(self, waypoints: list[dict]) -> bool:
        if not self.connection:
            return False
        conn = self.connection
        conn.waypoint_clear_all_send()
        conn.waypoint_count_send(len(waypoints))

        for i, wp in enumerate(waypoints):
            conn.recv_match(type=["MISSION_REQUEST"], blocking=True, timeout=5)
            conn.mav.mission_item_send(
                conn.target_system, conn.target_component,
                i,
                mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT,
                mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
                0, 1,  # current, autocontinue
                0, 0, 0, 0,  # params 1-4
                wp["lat"], wp["lon"], wp["alt"],
            )
        ack = conn.recv_match(type=["MISSION_ACK"], blocking=True, timeout=5)
        return ack is not None

    def download_mission(self) -> list[dict]:
        if not self.connection:
            return []
        conn = self.connection
        conn.waypoint_request_list_send()
        count_msg = conn.recv_match(type=["MISSION_COUNT"], blocking=True, timeout=5)
        if not count_msg:
            return []

        waypoints = []
        for i in range(count_msg.count):
            conn.waypoint_request_send(i)
            wp = conn.recv_match(type=["MISSION_ITEM"], blocking=True, timeout=5)
            if wp and wp.command == mavutil.mavlink.MAV_CMD_NAV_WAYPOINT:
                waypoints.append({"lat": wp.x, "lon": wp.y, "alt": wp.z})
        return waypoints


# Singleton instance
vehicle_manager = VehicleManager()

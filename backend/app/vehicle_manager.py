import threading
import time
from dataclasses import dataclass, field
from typing import Optional
from dronekit import connect, VehicleMode


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
        self.vehicle = None
        self.connected = False
        self.connection_string = None
        self.telemetry = TelemetryData()
        self._telemetry_thread = None
        self._running = False

    def connect(self, connection_string: str, baud: int = 57600) -> bool:
        try:
            self.vehicle = connect(connection_string, baud=baud, wait_ready=True, timeout=30)
            self.connected = True
            self.connection_string = connection_string
            self._start_telemetry_loop()
            return True
        except Exception as e:
            self.connected = False
            raise ConnectionError(f"Failed to connect: {e}")

    def disconnect(self):
        self._running = False
        if self._telemetry_thread:
            self._telemetry_thread.join(timeout=5)
        if self.vehicle:
            self.vehicle.close()
        self.vehicle = None
        self.connected = False
        self.connection_string = None

    def _start_telemetry_loop(self):
        self._running = True
        self._telemetry_thread = threading.Thread(target=self._update_telemetry, daemon=True)
        self._telemetry_thread.start()

    def _update_telemetry(self):
        while self._running and self.vehicle:
            try:
                v = self.vehicle
                loc = v.location.global_relative_frame
                att = v.attitude
                bat = v.battery
                gps = v.gps_0

                self.telemetry = TelemetryData(
                    armed=v.armed,
                    mode=v.mode.name if v.mode else "UNKNOWN",
                    altitude=loc.alt or 0.0,
                    airspeed=v.airspeed or 0.0,
                    groundspeed=v.groundspeed or 0.0,
                    heading=v.heading or 0,
                    lat=loc.lat or 0.0,
                    lon=loc.lon or 0.0,
                    battery_voltage=bat.voltage or 0.0,
                    battery_current=bat.current or 0.0,
                    battery_level=bat.level,
                    pitch=att.pitch or 0.0,
                    roll=att.roll or 0.0,
                    yaw=att.yaw or 0.0,
                    gps_fix=gps.fix_type or 0,
                    gps_satellites=gps.satellites_visible or 0,
                )
            except Exception:
                pass
            time.sleep(0.1)  # 10Hz update rate

    def set_mode(self, mode: str) -> bool:
        if not self.vehicle:
            return False
        self.vehicle.mode = VehicleMode(mode)
        return True

    def arm(self) -> bool:
        if not self.vehicle:
            return False
        self.vehicle.armed = True
        return True

    def disarm(self) -> bool:
        if not self.vehicle:
            return False
        self.vehicle.armed = False
        return True

    def get_available_modes(self) -> list[str]:
        return [
            "MANUAL", "STABILIZE", "FBWA", "FBWB", "AUTO",
            "RTL", "LOITER", "GUIDED", "CIRCLE", "LAND"
        ]


# Singleton instance
vehicle_manager = VehicleManager()

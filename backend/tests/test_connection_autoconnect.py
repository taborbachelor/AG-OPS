"""Auto-connect the Cube by USB VID (TASK-007).

COM numbering is not stable between boots, so the operator should never be
asked which port the Cube is on. These tests cover the identification rules
and -- more importantly -- the things that must NOT happen: dialling a
telemetry radio, clobbering a live link, or racing itself.

No hardware and no vehicle: `comports()` and `vehicle_manager.connect` are
both faked.
"""
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from app.main import app
from app.routers import connection
from app.vehicle_manager import vehicle_manager


def port(device, description="n/a", vid=None, pid=None, serial_number=None,
         manufacturer=None):
    """A pyserial ListPortInfo stand-in (only the fields the router reads)."""
    return SimpleNamespace(device=device, description=description, vid=vid,
                           pid=pid, serial_number=serial_number,
                           manufacturer=manufacturer)


# The Cube as Windows enumerates it, and two things that must never be mistaken
# for it: a SiK telemetry radio (FTDI bridge) and a motherboard serial port.
CUBE = port("COM3", "CubeOrange (COM3)", vid=connection.CUBE_VID, pid=0x1011,
            serial_number="2D003B", manufacturer="ProfiCNC")
SIK_RADIO = port("COM5", "USB Serial Port (COM5)", vid=0x0403, pid=0x6001,
                 manufacturer="FTDI")
BUILTIN = port("COM1", "Communications Port (COM1)")


def with_ports(*ports):
    return mock.patch("serial.tools.list_ports.comports", return_value=list(ports))


def idle_vehicle():
    """Disconnected and not mid-auto-reconnect."""
    return mock.patch.multiple(vehicle_manager, connected=False,
                               reconnecting=False)


class TestPortListing(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_original_shape_is_preserved(self):
        # The connection UI has always read device + description. Auto-connect
        # is additive; breaking these two breaks the manual form too.
        with with_ports(CUBE):
            r = self.client.get("/api/connection/ports")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()[0]["device"], "COM3")
        self.assertEqual(r.json()[0]["description"], "CubeOrange (COM3)")

    def test_cube_is_identified_and_gets_the_usb_baud(self):
        with with_ports(CUBE):
            p = self.client.get("/api/connection/ports").json()[0]
        self.assertTrue(p["is_flight_controller"])
        self.assertEqual(p["board"], "Cube (Hex/ProfiCNC)")
        self.assertEqual(p["suggested_baud"], 115200)

    def test_telemetry_radio_is_not_a_flight_controller(self):
        # An FTDI bridge is a radio, not a board. What is on the far end is
        # unknown, and 115200 would be the wrong rate for it.
        with with_ports(SIK_RADIO):
            p = self.client.get("/api/connection/ports").json()[0]
        self.assertFalse(p["is_flight_controller"])
        self.assertIsNone(p["board"])
        self.assertEqual(p["suggested_baud"], 57600)

    def test_port_with_no_vid_does_not_crash(self):
        # Built-in COM ports report vid=None.
        with with_ports(BUILTIN):
            p = self.client.get("/api/connection/ports").json()[0]
        self.assertIsNone(p["vid"])
        self.assertFalse(p["is_flight_controller"])


class TestDetect(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_picks_the_cube_out_of_a_populated_machine(self):
        with with_ports(BUILTIN, SIK_RADIO, CUBE):
            d = self.client.get("/api/connection/detect").json()
        self.assertTrue(d["found"])
        self.assertEqual(d["device"], "COM3")
        self.assertEqual(d["baud"], 115200)
        self.assertEqual(len(d["candidates"]), 1)
        self.assertEqual(len(d["ports"]), 3)

    def test_no_cube_is_a_200_not_an_error(self):
        # Asking is not doing. "Nothing plugged in" is a valid answer.
        with with_ports(BUILTIN, SIK_RADIO):
            r = self.client.get("/api/connection/detect")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["found"])
        self.assertIsNone(r.json()["device"])

    def test_candidates_sort_naturally_not_lexically(self):
        # A plain string sort makes COM10 come before COM2, so "the first
        # Cube" would change with the digit count.
        cube10 = port("COM10", "CubeOrange", vid=connection.CUBE_VID)
        cube2 = port("COM2", "CubeOrange", vid=connection.CUBE_VID)
        with with_ports(cube10, cube2):
            d = self.client.get("/api/connection/detect").json()
        self.assertEqual([c["device"] for c in d["candidates"]],
                         ["COM2", "COM10"])


class TestAutoConnect(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_connects_to_the_cube_at_115200_without_being_asked(self):
        with with_ports(BUILTIN, SIK_RADIO, CUBE), idle_vehicle(), \
             mock.patch.object(vehicle_manager, "connect",
                               return_value=True) as conn:
            r = self.client.post("/api/connection/autoconnect")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["connection"], "COM3")
        self.assertEqual(r.json()["board"], "Cube (Hex/ProfiCNC)")
        conn.assert_called_once_with("COM3", 115200)

    def test_never_dials_a_telemetry_radio(self):
        # The whole point of matching on VID: a machine with a radio and no
        # board must do nothing at all.
        with with_ports(BUILTIN, SIK_RADIO), idle_vehicle(), \
             mock.patch.object(vehicle_manager, "connect") as conn:
            r = self.client.post("/api/connection/autoconnect")
        self.assertEqual(r.status_code, 404)
        conn.assert_not_called()

    def test_not_found_message_names_the_ports_it_saw(self):
        # On a bench this message is the entire diagnosis.
        with with_ports(SIK_RADIO), idle_vehicle(), \
             mock.patch.object(vehicle_manager, "connect"):
            detail = self.client.post("/api/connection/autoconnect").json()["detail"]
        self.assertIn("0x2DAE", detail)
        self.assertIn("COM5", detail)
        self.assertIn("USB Serial Port (COM5)", detail)

    def test_already_connected_is_a_no_op_not_an_error(self):
        # The UI fires this on startup; a repeat must not redial a live link.
        with with_ports(CUBE), \
             mock.patch.multiple(vehicle_manager, connected=True,
                                 reconnecting=False,
                                 connection_string="COM3"), \
             mock.patch.object(vehicle_manager, "connect") as conn:
            r = self.client.post("/api/connection/autoconnect")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "already_connected")
        conn.assert_not_called()

    def test_refuses_while_the_auto_reconnect_thread_is_dialling(self):
        # Same guard as manual connect: racing it spawns duplicate telemetry
        # loops on one vehicle.
        with with_ports(CUBE), \
             mock.patch.multiple(vehicle_manager, connected=False,
                                 reconnecting=True), \
             mock.patch.object(vehicle_manager, "connect") as conn:
            r = self.client.post("/api/connection/autoconnect")
        self.assertEqual(r.status_code, 409)
        conn.assert_not_called()

    def test_falls_through_to_the_next_match_when_one_is_silent(self):
        # A board can expose more than one USB serial interface and only one
        # of them speaks MAVLink.
        cube_a = port("COM3", "CubeOrange", vid=connection.CUBE_VID)
        cube_b = port("COM4", "CubeOrange", vid=connection.CUBE_VID)

        def flaky(device, baud):
            if device == "COM3":
                raise ConnectionError("Failed to connect: no heartbeat from vehicle")
            return True

        with with_ports(cube_a, cube_b), idle_vehicle(), \
             mock.patch.object(vehicle_manager, "connect",
                               side_effect=flaky) as conn:
            r = self.client.post("/api/connection/autoconnect")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["connection"], "COM4")
        self.assertEqual(conn.call_count, 2)
        self.assertEqual(r.json()["attempts"][0]["device"], "COM3")

    def test_all_candidates_silent_reports_each_failure(self):
        with with_ports(CUBE), idle_vehicle(), \
             mock.patch.object(vehicle_manager, "connect",
                               side_effect=ConnectionError("no heartbeat from vehicle")):
            r = self.client.post("/api/connection/autoconnect")
        self.assertEqual(r.status_code, 500)
        self.assertIn("COM3", r.json()["detail"])
        self.assertIn("no heartbeat", r.json()["detail"])

    def test_is_single_flight(self):
        # StrictMode double-fires the UI's mount effect, so the overlapping
        # pair is the normal case, not an edge case. Both passing the
        # `connected` check would clobber the link.
        self.assertTrue(connection._autoconnect_lock.acquire(blocking=False))
        try:
            with with_ports(CUBE), idle_vehicle(), \
                 mock.patch.object(vehicle_manager, "connect") as conn:
                r = self.client.post("/api/connection/autoconnect")
        finally:
            connection._autoconnect_lock.release()
        self.assertEqual(r.status_code, 409)
        conn.assert_not_called()

    def test_lock_is_released_after_a_failed_attempt(self):
        # A 404 that left the lock held would make auto-connect work exactly
        # once per backend process.
        with with_ports(BUILTIN), idle_vehicle(), \
             mock.patch.object(vehicle_manager, "connect"):
            self.client.post("/api/connection/autoconnect")
        self.assertTrue(connection._autoconnect_lock.acquire(blocking=False))
        connection._autoconnect_lock.release()

    def test_baud_override_is_honoured_for_the_bench(self):
        with with_ports(CUBE), idle_vehicle(), \
             mock.patch.object(vehicle_manager, "connect",
                               return_value=True) as conn:
            r = self.client.post("/api/connection/autoconnect",
                                 json={"baud": 57600})
        self.assertEqual(r.status_code, 200)
        conn.assert_called_once_with("COM3", 57600)


class TestManualConnectUnaffected(unittest.TestCase):
    """Auto-connect is additive. The manual path is what the operator falls
    back to when the VID match is wrong, so it must not have moved."""

    def setUp(self):
        self.client = TestClient(app)

    def test_manual_connect_still_works(self):
        with idle_vehicle(), \
             mock.patch.object(vehicle_manager, "connect",
                               return_value=True) as conn:
            r = self.client.post("/api/connection/connect",
                                 json={"connection_string": "tcp:127.0.0.1:5760"})
        self.assertEqual(r.status_code, 200)
        conn.assert_called_once_with("tcp:127.0.0.1:5760", 57600)

    def test_manual_connect_still_rejects_when_already_connected(self):
        with mock.patch.multiple(vehicle_manager, connected=True,
                                 reconnecting=False), \
             mock.patch.object(vehicle_manager, "connect") as conn:
            r = self.client.post("/api/connection/connect",
                                 json={"connection_string": "COM3"})
        self.assertEqual(r.status_code, 400)
        conn.assert_not_called()


if __name__ == "__main__":
    unittest.main()

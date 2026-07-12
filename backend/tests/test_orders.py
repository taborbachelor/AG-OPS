"""Unit tests for the customer orders router.

Handlers are plain sync functions, so we call them directly — no test client
or event loop needed. Each test gets a fresh throwaway sqlite file via the
ORDERS_DB env override.

Run from backend/ with the venv active:
    python -m unittest tests.test_orders
"""

import math
import os
import tempfile
import unittest
from datetime import date, timedelta

from fastapi import HTTPException

from app.routers import orders
from app.routers.orders import FieldBoundary, LatLon, OrderCreate, StatusUpdate

# ~20-acre square anchored near Sabetha, KS. 20 acres = 80937.13 m^2, so the
# side is sqrt(80937.13) ~= 284.49 m; converted to degrees at lat 39.9042
# using the same spherical radius the server uses (dlon divided by cos(lat)).
_LAT0 = 39.9042
_LON0 = -95.7997
_SIDE_M = math.sqrt(20 * 4046.8564224)
_DEG = 6371000.0 * math.pi / 180.0            # metres per degree of latitude
_DLAT = _SIDE_M / _DEG
_DLON = _SIDE_M / (_DEG * math.cos(math.radians(_LAT0)))

TWENTY_ACRE_SQUARE = [
    LatLon(lat=_LAT0, lon=_LON0),
    LatLon(lat=_LAT0, lon=_LON0 + _DLON),
    LatLon(lat=_LAT0 + _DLAT, lon=_LON0 + _DLON),
    LatLon(lat=_LAT0 + _DLAT, lon=_LON0),
]

# A tiny triangle (a few metres across) — always priced at the job minimum.
TINY_TRIANGLE = [
    LatLon(lat=_LAT0, lon=_LON0),
    LatLon(lat=_LAT0 + 0.0001, lon=_LON0),
    LatLon(lat=_LAT0, lon=_LON0 + 0.0001),
]


def _future_date(days: int = 7) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _order_body(polygon=None, **overrides) -> OrderCreate:
    payload = {
        "name": "Ada Grower",
        "email": "ada@example.com",
        "phone": "785-555-0100",
        "field": {"polygon": [p.model_dump() for p in (polygon or TINY_TRIANGLE)]},
        "date": _future_date(),
        "slot": "AM",
    }
    payload.update(overrides)
    return OrderCreate.model_validate(payload)


class OrdersApiTest(unittest.TestCase):

    def setUp(self):
        # Point the router at a throwaway sqlite file so tests never touch
        # (or depend on) the real backend/data/orders.db.
        fd, self._db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.environ["ORDERS_DB"] = self._db

    def tearDown(self):
        os.environ.pop("ORDERS_DB", None)
        try:
            os.remove(self._db)
        except OSError:
            pass  # Windows can briefly hold the file; temp dir cleans up.

    # -- lifecycle ----------------------------------------------------------

    def test_happy_path_create_pay_and_advance_to_done(self):
        order = orders.create_order(_order_body())
        self.assertEqual(order["status"], "pending_payment")
        self.assertTrue(order["id"])

        # Round-trips through the DB, both by id and by email.
        self.assertEqual(orders.get_order(order["id"])["id"], order["id"])
        listed = orders.list_orders(email="ada@example.com")["orders"]
        self.assertEqual([o["id"] for o in listed], [order["id"]])

        self.assertEqual(orders.pay_order(order["id"])["status"], "paid")
        for nxt in ("scheduled", "in_progress", "done"):
            result = orders.update_status(order["id"], StatusUpdate(status=nxt))
            self.assertEqual(result["status"], nxt)
        self.assertEqual(orders.get_order(order["id"])["status"], "done")

    # -- pricing ------------------------------------------------------------

    def test_price_formula_for_twenty_acre_polygon(self):
        order = orders.create_order(_order_body(polygon=TWENTY_ACRE_SQUARE))
        acres = order["acres"]
        self.assertAlmostEqual(acres, 20.0, delta=0.2)
        self.assertEqual(order["price_cents"], max(round(acres * 1200), 15000))
        # 20 acres is well above the $150 floor, so the per-acre rate applies.
        self.assertGreater(order["price_cents"], 15000)

    def test_minimum_price_applies_to_tiny_field(self):
        order = orders.create_order(_order_body(polygon=TINY_TRIANGLE))
        self.assertEqual(order["price_cents"], 15000)

    def test_client_supplied_price_is_ignored(self):
        # A tampered client sends its own price fields; the model has no such
        # fields, so pydantic drops them and the server computes the price.
        body = _order_body(polygon=TWENTY_ACRE_SQUARE,
                           price=1, price_cents=1)
        order = orders.create_order(body)
        self.assertEqual(order["price_cents"],
                         max(round(order["acres"] * 1200), 15000))
        self.assertNotEqual(order["price_cents"], 1)

    # -- transitions --------------------------------------------------------

    def test_illegal_transition_raises_409(self):
        order = orders.create_order(_order_body())
        # Can't schedule an order that hasn't been paid for.
        with self.assertRaises(HTTPException) as ctx:
            orders.update_status(order["id"], StatusUpdate(status="scheduled"))
        self.assertEqual(ctx.exception.status_code, 409)

        # Can't skip a link in the chain either.
        orders.pay_order(order["id"])
        with self.assertRaises(HTTPException) as ctx:
            orders.update_status(order["id"], StatusUpdate(status="done"))
        self.assertEqual(ctx.exception.status_code, 409)

        # Double-payment is also a 409.
        with self.assertRaises(HTTPException) as ctx:
            orders.pay_order(order["id"])
        self.assertEqual(ctx.exception.status_code, 409)

    # -- validation ---------------------------------------------------------

    def test_past_date_rejected(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        with self.assertRaises(HTTPException) as ctx:
            orders.create_order(_order_body(date=yesterday))
        self.assertEqual(ctx.exception.status_code, 400)
        # Today is also too soon — the earliest bookable day is tomorrow.
        with self.assertRaises(HTTPException):
            orders.create_order(_order_body(date=date.today().isoformat()))

    def test_two_point_polygon_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            orders.create_order(_order_body(polygon=TINY_TRIANGLE[:2]))
        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()

"""Customer orders API for the autonomous field-spraying service.

This router is intentionally NOT registered in app.main yet — the operator
GCS backend stays untouched until the customer flow is ready to ship. When it
is, wire it up with:

    app.include_router(orders.router, prefix="/api/orders", tags=["orders"])

The customer site (web/) reaches this API via a Vite dev proxy ('/api' ->
http://localhost:8000), so its requests arrive same-origin and need no CORS
entry. If the site is ever served from its own origin instead (e.g.
http://localhost:3001 without the proxy), that origin must also be added to
allow_origins in app.main at integration time.

Storage is stdlib sqlite3 so the scaffold adds zero dependencies. Handlers are
plain sync ``def`` so unit tests can call them directly without an event loop.
"""

import json
import math
import os
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter()

# --- Pricing (SERVER-SIDE ONLY) ---
# The website shows an *estimate*, but the authoritative price is always
# computed here from the server-measured acreage. Clients cannot send a price:
# the request models below simply have no price field, and pydantic drops any
# extra keys, so a tampered payload cannot influence what we charge.
PRICE_PER_ACRE_CENTS = 1200   # $12.00 per acre
MIN_PRICE_CENTS = 15000       # $150.00 minimum per job (covers mobilization)

# Order lifecycle: pending_payment -> paid -> scheduled -> in_progress -> done.
# /pay performs the first hop; /status walks the rest one link at a time so an
# order can never skip ahead or move backwards.
_STATUS_CHAIN = {
    "paid": "scheduled",
    "scheduled": "in_progress",
    "in_progress": "done",
}

_EARTH_RADIUS_M = 6371000.0
_SQ_M_PER_ACRE = 4046.8564224


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class LatLon(BaseModel):
    lat: float
    lon: float


class FieldBoundary(BaseModel):
    polygon: list[LatLon]


class OrderCreate(BaseModel):
    # Note: no price field on purpose — see pricing comment above.
    name: str
    email: str
    phone: Optional[str] = None
    field: FieldBoundary
    date: str            # 'YYYY-MM-DD'
    slot: str            # 'AM' | 'PM'


class StatusUpdate(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _db_path() -> str:
    """Resolve the sqlite file, honouring the ORDERS_DB env override.

    The override is read at call time (not import time) so tests can point at
    a throwaway file after importing this module. Default resolves to
    backend/data/orders.db.
    """
    override = os.environ.get("ORDERS_DB")
    if override:
        return override
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "data", "orders.db"))


def _connect() -> sqlite3.Connection:
    """Open the orders DB, creating the data/ dir and schema on first use."""
    path = _db_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id            TEXT PRIMARY KEY,
            name          TEXT,
            email         TEXT,
            phone         TEXT,
            field_geojson TEXT,
            acres         REAL,
            date          TEXT,
            slot          TEXT,
            status        TEXT,
            price_cents   INTEGER,
            created       TEXT
        )
        """
    )
    return conn


def _row_to_order(row: sqlite3.Row) -> dict:
    return dict(row)


def _fetch_order(conn: sqlite3.Connection, order_id: str) -> dict:
    """Load one order or raise 404. Parameterized SQL only — never interpolate."""
    row = conn.execute(
        "SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Order not found")
    return _row_to_order(row)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def _polygon_acres(polygon: list[LatLon]) -> float:
    """Area of a lat/lon polygon in acres.

    Fields are a few hundred metres across, so an equirectangular projection
    centred on the polygon's mean latitude (x scaled by cos(lat)) is accurate
    to well under 0.1% — no need for a geodesic library. Shoelace on the
    projected points gives m^2, then convert to acres.
    """
    lat0 = math.radians(sum(p.lat for p in polygon) / len(polygon))
    pts = [
        (math.radians(p.lon) * _EARTH_RADIUS_M * math.cos(lat0),
         math.radians(p.lat) * _EARTH_RADIUS_M)
        for p in polygon
    ]
    area2 = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        area2 += x1 * y2 - x2 * y1
    return abs(area2) / 2.0 / _SQ_M_PER_ACRE


def _price_cents(acres: float) -> int:
    """Authoritative price: per-acre rate with a job minimum."""
    return max(round(acres * PRICE_PER_ACRE_CENTS), MIN_PRICE_CENTS)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_new_order(body: OrderCreate) -> None:
    """Reject bad input with a 400 before anything touches the database."""
    if len(body.field.polygon) < 3:
        raise HTTPException(400, "Field polygon needs at least 3 points")
    # Pydantic's lax mode coerces the JSON strings "NaN"/"Infinity" into real
    # float nan/inf, which would blow up _polygon_acres/_price_cents with an
    # unhandled 500 — reject them here as ordinary bad input instead.
    for p in body.field.polygon:
        if not (math.isfinite(p.lat) and math.isfinite(p.lon)):
            raise HTTPException(400, "Field coordinates must be finite numbers")
    if "@" not in body.email:
        raise HTTPException(400, "A valid email address is required")
    if body.slot not in ("AM", "PM"):
        raise HTTPException(400, "Slot must be 'AM' or 'PM'")
    try:
        requested = date.fromisoformat(body.date)
    except ValueError:
        raise HTTPException(400, "Date must be a real date in YYYY-MM-DD format")
    # On Python 3.11+ fromisoformat also accepts compact ('20260901') and
    # ISO-week ('2026-W40-1') forms. The raw string is what gets stored, so
    # insist on the canonical YYYY-MM-DD spelling to keep the DB column
    # sortable and parseable by strict consumers downstream.
    if requested.isoformat() != body.date:
        raise HTTPException(400, "Date must be a real date in YYYY-MM-DD format")
    # Same-day dispatch isn't offered — the earliest bookable day is tomorrow.
    if requested < date.today() + timedelta(days=1):
        raise HTTPException(400, "Date must be tomorrow or later")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/")
def create_order(body: OrderCreate) -> dict:
    """Create an order: acreage and price are computed server-side."""
    _validate_new_order(body)

    acres = _polygon_acres(body.field.polygon)
    # Store the boundary as GeoJSON (lon, lat order per the spec) so the GCS
    # can later turn it directly into a spray mission.
    ring = [[p.lon, p.lat] for p in body.field.polygon]
    ring.append(ring[0])  # GeoJSON rings are closed
    field_geojson = json.dumps({"type": "Polygon", "coordinates": [ring]})

    order = {
        "id": uuid.uuid4().hex,
        "name": body.name,
        "email": body.email,
        "phone": body.phone,
        "field_geojson": field_geojson,
        "acres": acres,
        "date": body.date,
        "slot": body.slot,
        "status": "pending_payment",
        "price_cents": _price_cents(acres),
        "created": datetime.now(timezone.utc).isoformat(),
    }

    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO orders
                (id, name, email, phone, field_geojson, acres,
                 date, slot, status, price_cents, created)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (order["id"], order["name"], order["email"], order["phone"],
             order["field_geojson"], order["acres"], order["date"],
             order["slot"], order["status"], order["price_cents"],
             order["created"]),
        )
        conn.commit()
    finally:
        conn.close()
    return order


@router.get("/")
def list_orders(email: str = Query(...)) -> dict:
    """List all orders placed under an email address (customer lookup)."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM orders WHERE email = ? ORDER BY created",
            (email,)).fetchall()
        return {"orders": [_row_to_order(r) for r in rows]}
    finally:
        conn.close()


@router.get("/{order_id}")
def get_order(order_id: str) -> dict:
    """Fetch a single order by id, or 404."""
    conn = _connect()
    try:
        return _fetch_order(conn, order_id)
    finally:
        conn.close()


@router.post("/{order_id}/pay")
def pay_order(order_id: str) -> dict:
    """Mark an order as paid.

    ==========================================================================
    DEV-MODE SIMULATED PAYMENT — NOT FOR PRODUCTION.
    Once Stripe keys exist, this endpoint should instead create a Stripe
    Checkout Session (stripe.checkout.Session.create with amount =
    price_cents, currency='usd') and return its URL; the pending_payment ->
    paid transition then moves into the Stripe webhook handler for
    'checkout.session.completed'. Until then we flip the status directly so
    the rest of the pipeline can be built and demoed end-to-end.
    ==========================================================================
    """
    conn = _connect()
    try:
        order = _fetch_order(conn, order_id)
        if order["status"] != "pending_payment":
            raise HTTPException(
                409, f"Order is '{order['status']}', not awaiting payment")
        conn.execute(
            "UPDATE orders SET status = ? WHERE id = ?", ("paid", order_id))
        conn.commit()
        order["status"] = "paid"
        return order
    finally:
        conn.close()


@router.post("/{order_id}/status")
def update_status(order_id: str, body: StatusUpdate) -> dict:
    """Advance an order one step along paid -> scheduled -> in_progress -> done.

    Anything else — skipping steps, moving backwards, unknown statuses, or
    touching an unpaid order — is a 409 so callers can't corrupt the pipeline.
    """
    conn = _connect()
    try:
        order = _fetch_order(conn, order_id)
        allowed_next = _STATUS_CHAIN.get(order["status"])
        if allowed_next is None or body.status != allowed_next:
            raise HTTPException(
                409,
                f"Cannot move order from '{order['status']}' to '{body.status}'")
        conn.execute(
            "UPDATE orders SET status = ? WHERE id = ?", (body.status, order_id))
        conn.commit()
        order["status"] = body.status
        return order
    finally:
        conn.close()

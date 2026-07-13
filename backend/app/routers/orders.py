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

# The service area is northeast Kansas, so a "spray day" is a US Central
# calendar day. Date validation must use this zone — not the server's local
# zone (UTC in prod) — or an evening Central-time customer gets "tomorrow"
# rejected because the server's date has already rolled over. The website
# (web/src/geometry.js tomorrowISO) computes its earliest offered date in the
# same zone so client and server always agree.
try:
    from zoneinfo import ZoneInfo
    _BUSINESS_TZ = ZoneInfo("America/Chicago")
except Exception:
    # No IANA tz database (e.g. Windows without the tzdata package): fall back
    # to fixed CST (UTC-6). During daylight saving this puts the day boundary
    # at 1 AM Central — never rejects a valid "tomorrow", only briefly lenient.
    _BUSINESS_TZ = timezone(timedelta(hours=-6))


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


def _cross(o, a, b) -> float:
    """2D cross product of vectors o->a and o->b (orientation sign)."""
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _on_segment(a, b, p) -> bool:
    """True if collinear point p lies within segment ab's bounding box."""
    return (min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
            and min(a[1], b[1]) <= p[1] <= max(a[1], b[1]))


def _segments_intersect(p, q, r, s) -> bool:
    """True if closed segments pq and rs share any point."""
    d1 = _cross(r, s, p)
    d2 = _cross(r, s, q)
    d3 = _cross(p, q, r)
    d4 = _cross(p, q, s)
    if (((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0))
            and ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0))):
        return True
    if d1 == 0 and _on_segment(r, s, p):
        return True
    if d2 == 0 and _on_segment(r, s, q):
        return True
    if d3 == 0 and _on_segment(p, q, r):
        return True
    if d4 == 0 and _on_segment(p, q, s):
        return True
    return False


def _ring_self_intersects(polygon: list[LatLon]) -> bool:
    """True if the closed ring through the points crosses or touches itself.

    The shoelace in _polygon_acres silently *understates* a self-intersecting
    ring (a bow-tie's lobes cancel to ~0 acres -> the $150 floor), and the ring
    would be persisted as invalid GeoJSON for the future spray-mission
    pipeline — so such boundaries must be rejected outright. Non-adjacent edge
    pairs are checked pairwise; O(n^2) is fine for hand-clicked fields.
    Intersection is tested on raw (lon, lat) coordinates: the map projection
    only rescales the axes, which cannot create or remove a crossing.

    Exact consecutive duplicate points (e.g. a double-clicked corner in the
    map UI) are collapsed first: a zero-length edge shifts edge adjacency so
    the exemptions below would misread two genuinely-adjacent edges as
    non-adjacent and falsely flag a valid ring. Dropping such an edge cannot
    create or remove a real crossing — the traced path is unchanged.
    """
    pts = [(p.lon, p.lat) for p in polygon]
    dedup: list[tuple[float, float]] = []
    for pt in pts:
        if dedup and dedup[-1] == pt:
            continue
        dedup.append(pt)
    # The ring is implicitly closed, so a last point repeating the first is
    # the same degenerate zero-length (wrap) edge — drop it too.
    while len(dedup) > 1 and dedup[0] == dedup[-1]:
        dedup.pop()
    pts = dedup
    n = len(pts)
    if n < 4:
        return False  # a triangle cannot self-intersect
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue  # adjacent edges legitimately share an endpoint
            c, d = pts[j], pts[(j + 1) % n]
            if _segments_intersect(a, b, c, d):
                return True
    return False


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
    if _ring_self_intersects(body.field.polygon):
        raise HTTPException(
            400, "Field boundary must not cross itself — please redraw it")
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
    # Same-day dispatch isn't offered — the earliest bookable day is tomorrow,
    # measured in the service area's timezone (see _BUSINESS_TZ above), so a
    # Central-time customer's "tomorrow" is never rejected by a UTC server.
    business_today = datetime.now(_BUSINESS_TZ).date()
    if requested < business_today + timedelta(days=1):
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
        order = _fetch_order(conn, order_id)  # 404 if unknown
        # Atomic guard: the status predicate makes check-and-transition a
        # single statement, so concurrent /pay calls can't all pass a separate
        # read-then-write check (and a delayed writer can't clobber a later
        # status) — exactly one request flips the row.
        cur = conn.execute(
            "UPDATE orders SET status = ? WHERE id = ? AND status = ?",
            ("paid", order_id, "pending_payment"))
        conn.commit()
        if cur.rowcount != 1:
            order = _fetch_order(conn, order_id)
            raise HTTPException(
                409, f"Order is '{order['status']}', not awaiting payment")
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
        # Atomic guard (same as pay_order): only transition if the row still
        # holds the status we validated against, so racing callers can't each
        # apply the same step or overwrite a newer state.
        cur = conn.execute(
            "UPDATE orders SET status = ? WHERE id = ? AND status = ?",
            (body.status, order_id, order["status"]))
        conn.commit()
        if cur.rowcount != 1:
            order = _fetch_order(conn, order_id)
            raise HTTPException(
                409,
                f"Cannot move order from '{order['status']}' to '{body.status}'")
        order["status"] = body.status
        return order
    finally:
        conn.close()

// Field geometry + pricing shared by the map and the review step.
//
// This intentionally mirrors backend/app/routers/orders.py (shoelace on an
// equirectangular projection, same constants) so the estimate a customer sees
// matches the authoritative price the server computes. The server's number
// always wins — everything here is display-only.

const EARTH_RADIUS_M = 6371000
const SQ_M_PER_ACRE = 4046.8564224

export const PRICE_PER_ACRE_CENTS = 1200 // $12.00 / acre
export const MIN_PRICE_CENTS = 15000 // $150.00 job minimum

/** Area of a [{lat, lon}] polygon in acres (0 if fewer than 3 points). */
export function polygonAcres(points) {
  if (!points || points.length < 3) return 0
  const lat0 =
    (points.reduce((sum, p) => sum + p.lat, 0) / points.length) * (Math.PI / 180)
  const cosLat = Math.cos(lat0)
  const xy = points.map((p) => [
    (p.lon * Math.PI / 180) * EARTH_RADIUS_M * cosLat,
    (p.lat * Math.PI / 180) * EARTH_RADIUS_M,
  ])
  let twiceArea = 0
  for (let i = 0; i < xy.length; i++) {
    const [x1, y1] = xy[i]
    const [x2, y2] = xy[(i + 1) % xy.length]
    twiceArea += x1 * y2 - x2 * y1
  }
  return Math.abs(twiceArea) / 2 / SQ_M_PER_ACRE
}

/** Client-side price estimate in cents — same formula as the server. */
export function estimateCents(acres) {
  return Math.max(Math.round(acres * PRICE_PER_ACRE_CENTS), MIN_PRICE_CENTS)
}

/** Format cents as US dollars, e.g. 15000 -> "$150.00". */
export function formatUSD(cents) {
  return (cents / 100).toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
  })
}

/**
 * True if the closed ring through [{lat, lon}] points crosses or touches
 * itself (e.g. a bow-tie from clicking corners in a Z order). The shoelace
 * above silently *understates* such a ring's area — lobes cancel toward 0
 * acres — and the server rejects it, so the UI must catch it before review.
 * Tested on raw (lon, lat): projection only rescales axes, which cannot
 * create or remove a crossing. Mirrors _ring_self_intersects in
 * backend/app/routers/orders.py.
 *
 * Exact consecutive duplicate points (e.g. a double-clicked corner on the
 * map) are collapsed first: a zero-length edge shifts edge adjacency so the
 * adjacency exemptions below would misread two genuinely-adjacent edges as
 * non-adjacent and falsely flag a valid ring. Dropping such an edge cannot
 * create or remove a real crossing — the traced path is unchanged.
 */
export function ringSelfIntersects(points) {
  const raw = (points || []).map((p) => [p.lon, p.lat])
  const pts = []
  for (const pt of raw) {
    const prev = pts[pts.length - 1]
    if (prev && prev[0] === pt[0] && prev[1] === pt[1]) continue
    pts.push(pt)
  }
  // The ring is implicitly closed, so a last point repeating the first is
  // the same degenerate zero-length (wrap) edge — drop it too.
  while (
    pts.length > 1 &&
    pts[0][0] === pts[pts.length - 1][0] &&
    pts[0][1] === pts[pts.length - 1][1]
  ) {
    pts.pop()
  }
  const n = pts.length
  if (n < 4) return false // a triangle cannot self-intersect
  const cross = (o, a, b) =>
    (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
  const onSeg = (a, b, p) =>
    Math.min(a[0], b[0]) <= p[0] && p[0] <= Math.max(a[0], b[0]) &&
    Math.min(a[1], b[1]) <= p[1] && p[1] <= Math.max(a[1], b[1])
  const segsIntersect = (p, q, r, s) => {
    const d1 = cross(r, s, p)
    const d2 = cross(r, s, q)
    const d3 = cross(p, q, r)
    const d4 = cross(p, q, s)
    if (
      ((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) &&
      ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0))
    ) {
      return true
    }
    if (d1 === 0 && onSeg(r, s, p)) return true
    if (d2 === 0 && onSeg(r, s, q)) return true
    if (d3 === 0 && onSeg(p, q, r)) return true
    if (d4 === 0 && onSeg(p, q, s)) return true
    return false
  }
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      // Adjacent edges legitimately share an endpoint — skip those pairs.
      if (j === i + 1 || (i === 0 && j === n - 1)) continue
      if (segsIntersect(pts[i], pts[(i + 1) % n], pts[j], pts[(j + 1) % n])) {
        return true
      }
    }
  }
  return false
}

// The service area is northeast Kansas, so "tomorrow" (the earliest bookable
// spray day) is a US Central calendar day. The server validates against the
// same zone (see _BUSINESS_TZ in backend/app/routers/orders.py) — computing
// this from the browser's local date instead made the server reject the very
// date the UI offered whenever the two zones disagreed.
const BUSINESS_TIME_ZONE = 'America/Chicago'

/** Tomorrow in the service area's timezone as a YYYY-MM-DD string. */
export function tomorrowISO() {
  const now = new Date()
  let y = now.getFullYear()
  let m = now.getMonth() + 1
  let d = now.getDate()
  try {
    // 'en-CA' formats as YYYY-MM-DD; timeZone gives today's date in Kansas.
    const today = new Intl.DateTimeFormat('en-CA', {
      timeZone: BUSINESS_TIME_ZONE,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).format(now)
    ;[y, m, d] = today.split('-').map(Number)
  } catch {
    /* Intl timezone unavailable — fall back to the browser-local date. */
  }
  const t = new Date(Date.UTC(y, m - 1, d + 1)) // Date.UTC rolls months/years
  const mm = String(t.getUTCMonth() + 1).padStart(2, '0')
  const dd = String(t.getUTCDate()).padStart(2, '0')
  return `${t.getUTCFullYear()}-${mm}-${dd}`
}

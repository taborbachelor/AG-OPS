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

/** Tomorrow as a local-time YYYY-MM-DD string (min value for the date input). */
export function tomorrowISO() {
  const d = new Date()
  d.setDate(d.getDate() + 1)
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${mm}-${dd}`
}

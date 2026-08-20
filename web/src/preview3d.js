// Pure geometry for the 3D field preview.
//
// Deliberately free of any Cesium import: this is the part with decisions in
// it (what altitude the path is drawn at, where the camera sits), and it is
// testable in jsdom, where Cesium's WebGL runtime cannot load at all. The
// component in FieldPreview3D.jsx is a thin shell over these functions.

const EARTH_RADIUS_M = 6371000
const DEG = Math.PI / 180

/**
 * Ground altitude for the field outline, in metres.
 *
 * The preview draws on Cesium's plain ellipsoid with NO terrain provider —
 * the same arrangement as the operator GCS (MapView3D.jsx sets no
 * terrainProvider either). Field and path therefore share one flat reference,
 * so the height difference a customer sees between them is exactly the spray
 * altitude and nothing else.
 */
export const GROUND_ALT_M = 0

/**
 * Altitudes for the spray path, READ from the plan's waypoints.
 *
 * Never guessed, and never scaled for looks. The operator GCS shipped exactly
 * that bug: MapView3D.jsx defaulted a missing altitude to 60 m and drew a
 * climb the aircraft does not fly (fixed in TASK-024; backend/app/
 * coverage_multi.py now states an explicit alt on every point). A customer
 * preview that exaggerated height to look impressive would be the same lie
 * with a nicer motive, so a waypoint with no altitude is reported as missing
 * and the caller says so, rather than being quietly floated to a nice number.
 *
 * Returns {positions: [[lat, lon, alt], ...], altitudes: [...], missing: bool}.
 */
export function planPathPoints(plan) {
  const wps = (plan && plan.waypoints) || []
  const positions = []
  let missing = false
  for (const w of wps) {
    if (w.alt == null) missing = true
    positions.push([w.lat, w.lon, w.alt == null ? GROUND_ALT_M : w.alt])
  }
  return {
    positions,
    altitudes: positions.map((p) => p[2]),
    missing,
  }
}

/**
 * The single altitude a spray plan flies at, or null if it has none / varies.
 *
 * Used for the caption under the map. A plan whose waypoints disagree gets
 * null rather than an average: "flying at 20 m" is a claim, and averaging two
 * different numbers into it would make the caption say something no waypoint
 * actually says.
 */
export function planAltitude(plan) {
  const { altitudes, missing } = planPathPoints(plan)
  if (missing || altitudes.length === 0) return null
  const first = altitudes[0]
  return altitudes.every((a) => a === first) ? first : null
}

/** Closed [{lat, lon}] ring as a flat [lat, lon, lat, lon, ...] list. */
export function ringLatLonFlat(vertices) {
  const out = []
  for (const v of vertices || []) out.push(v.lat, v.lon)
  return out
}

/**
 * Where to put the camera so the whole field is in frame.
 *
 * Returns {lat, lon, range} — the field's centre and a viewing distance in
 * metres. `range` is the field's largest horizontal extent scaled up so the
 * boundary does not touch the edge of the canvas, with a floor so a tiny
 * field does not park the camera inside the ground.
 */
export function cameraTarget(vertices, { padding = 2.2, minRange = 220 } = {}) {
  const pts = vertices || []
  if (pts.length === 0) return null
  let minLat = Infinity, maxLat = -Infinity, minLon = Infinity, maxLon = -Infinity
  for (const p of pts) {
    if (p.lat < minLat) minLat = p.lat
    if (p.lat > maxLat) maxLat = p.lat
    if (p.lon < minLon) minLon = p.lon
    if (p.lon > maxLon) maxLon = p.lon
  }
  const lat = (minLat + maxLat) / 2
  const lon = (minLon + maxLon) / 2
  const cosLat = Math.cos(lat * DEG)
  const spanNS = (maxLat - minLat) * DEG * EARTH_RADIUS_M
  const spanEW = (maxLon - minLon) * DEG * EARTH_RADIUS_M * cosLat
  const range = Math.max(Math.max(spanNS, spanEW) * padding, minRange)
  return { lat, lon, range }
}

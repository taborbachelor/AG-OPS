import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

// Same imagery as the drawing step so the review map looks familiar.
const ESRI_WORLD_IMAGERY =
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'

// Preview assumptions sent to the planner. The spray drone covers ~40 m per
// pass; ops may still tune swath/altitude before flight, so the copy below
// is deliberately phrased as an "about" estimate.
const PREVIEW_SWATH_M = 40
const PREVIEW_ALT_M = 100

const GREEN = '#1a7f37'
const GREEN_LIGHT = '#4cae4f'
// Amber path pops against both satellite imagery and the green field fill.
const PATH_COLOR = '#ffb703'

/**
 * Read-only review map: the customer's field plus the serpentine spray path
 * the drone will actually fly, fetched from POST /api/coverage/plan.
 *
 * The preview is strictly decorative — any fetch failure collapses to a
 * subtle "unavailable" note and never blocks the checkout flow.
 *
 * Props:
 *   vertices  [{lat, lon}] closed field boundary (>= 3 points)
 */
export default function SprayPlanPreview({ vertices }) {
  const containerRef = useRef(null)
  const mapRef = useRef(null)
  const pathLayerRef = useRef(null)

  // null = still loading, false = failed, object = {waypoints, stats}
  const [plan, setPlan] = useState(null)

  // Create the map framed on the field; tear it down on unmount. In practice
  // this runs once — editing the field unmounts the review step, so vertices
  // are fixed for one map instance's life — but rebuilding on change is the
  // correct behaviour anyway.
  useEffect(() => {
    const map = L.map(containerRef.current, {
      // Scroll-wheel zoom on a small embedded map mostly hijacks page
      // scrolling; pinch/double-click zoom still work.
      scrollWheelZoom: false,
    })
    L.tileLayer(ESRI_WORLD_IMAGERY, {
      maxZoom: 19,
      attribution:
        'Imagery &copy; Esri, Maxar, Earthstar Geographics, and the GIS User Community',
    }).addTo(map)

    const latlngs = vertices.map((v) => [v.lat, v.lon])
    const field = L.polygon(latlngs, {
      color: GREEN,
      weight: 3,
      fillColor: GREEN_LIGHT,
      fillOpacity: 0.2,
    }).addTo(map)
    map.fitBounds(field.getBounds(), { padding: [24, 24] })

    pathLayerRef.current = L.layerGroup().addTo(map)
    mapRef.current = map
    return () => map.remove()
  }, [vertices])

  // Ask the planner for the real spray path. Cancelled flag guards against
  // setState after unmount (and StrictMode's dev double-mount).
  useEffect(() => {
    let cancelled = false
    setPlan(null)
    fetch('/api/coverage/plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        polygon: vertices,
        swath: PREVIEW_SWATH_M,
        alt: PREVIEW_ALT_M,
      }),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`coverage API returned ${res.status}`)
        return res.json()
      })
      .then((data) => {
        if (!cancelled) setPlan(data)
      })
      .catch(() => {
        if (!cancelled) setPlan(false)
      })
    return () => {
      cancelled = true
    }
  }, [vertices])

  // Overlay the returned waypoints as the flight path once they arrive.
  useEffect(() => {
    const layer = pathLayerRef.current
    if (!layer) return
    layer.clearLayers()
    if (!plan || !plan.waypoints || plan.waypoints.length < 2) return

    const path = plan.waypoints.map((w) => [w.lat, w.lon])
    L.polyline(path, { color: PATH_COLOR, weight: 2.5, opacity: 0.95 }).addTo(layer)
    // Mark where the pattern starts so the polyline reads as a route.
    L.circleMarker(path[0], {
      radius: 5,
      color: '#ffffff',
      weight: 2,
      fillColor: PATH_COLOR,
      fillOpacity: 1,
    }).addTo(layer)
  }, [plan])

  const stats = plan ? plan.stats : null
  const minutes = stats ? Math.max(1, Math.round(stats.est_time_s / 60)) : 0

  return (
    <div className="plan-preview">
      <div ref={containerRef} className="plan-map" aria-label="Spray path preview map" />
      {stats ? (
        <p className="plan-note">
          <strong>
            Your drone will fly {stats.n_passes} pass{stats.n_passes === 1 ? '' : 'es'}
          </strong>{' '}
          — about {minutes} minute{minutes === 1 ? '' : 's'} in the air.
        </p>
      ) : plan === false ? (
        <p className="plan-note muted">
          Spray-path preview unavailable right now — this doesn&rsquo;t affect
          your booking.
        </p>
      ) : (
        <p className="plan-note muted">Planning your drone&rsquo;s route…</p>
      )}
    </div>
  )
}

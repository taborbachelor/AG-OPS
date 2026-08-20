import { useEffect, useRef } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

// Same imagery as the drawing step so the review map looks familiar. The 3D
// view (FieldPreview3D.jsx) uses this identical tile URL for the same reason.
const ESRI_WORLD_IMAGERY =
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'

const GREEN = '#1a7f37'
const GREEN_LIGHT = '#4cae4f'
// Amber path pops against both satellite imagery and the green field fill.
const PATH_COLOR = '#ffb703'

/**
 * Top-down review map: the customer's field plus the serpentine spray path.
 *
 * Extracted from SprayPlanPreview when the 3D view was added, so the two
 * renderers are siblings over one shared plan rather than one growing a second
 * mode inside itself. Leaflet ignores waypoint altitude entirely — that is the
 * whole reason the 3D view exists.
 *
 * Props:
 *   vertices  [{lat, lon}] closed field boundary (>= 3 points)
 *   plan      the /api/coverage/plan response, or null/false
 */
export default function PlanMap2D({ vertices, plan }) {
  const containerRef = useRef(null)
  const mapRef = useRef(null)
  const pathLayerRef = useRef(null)

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

  return (
    <div ref={containerRef} className="plan-map" aria-label="Spray path preview map" />
  )
}

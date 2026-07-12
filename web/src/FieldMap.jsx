import { useEffect, useRef } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

// Satellite imagery so growers recognise their own field edges.
const ESRI_WORLD_IMAGERY =
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'

// Default view: Sabetha, KS — the middle of our initial service area.
const DEFAULT_CENTER = [39.9042, -95.7997]
const DEFAULT_ZOOM = 14

// Clicking within this many screen pixels of the first vertex closes the ring.
const CLOSE_SNAP_PX = 14

const GREEN = '#1a7f37'
const GREEN_LIGHT = '#4cae4f'

/**
 * Plain-Leaflet field tracer (no react-leaflet). React owns the vertex list;
 * this component owns the Leaflet map instance and redraws markers/outline
 * whenever the vertices change.
 *
 * Props:
 *   vertices    [{lat, lon}] placed so far
 *   closed      boolean — polygon finished
 *   onAddVertex ({lat, lon}) => void
 *   onClose     () => void — user clicked near the first vertex
 */
export default function FieldMap({ vertices, closed, onAddVertex, onClose }) {
  const containerRef = useRef(null)
  const mapRef = useRef(null)
  const drawLayerRef = useRef(null)

  // Refs mirror the latest props so the (created-once) click handler never
  // sees a stale closure.
  const stateRef = useRef({ vertices, closed, onAddVertex, onClose })
  stateRef.current = { vertices, closed, onAddVertex, onClose }

  // Create the map once; tear it down on unmount.
  useEffect(() => {
    const map = L.map(containerRef.current).setView(DEFAULT_CENTER, DEFAULT_ZOOM)
    L.tileLayer(ESRI_WORLD_IMAGERY, {
      maxZoom: 19,
      attribution:
        'Imagery &copy; Esri, Maxar, Earthstar Geographics, and the GIS User Community',
    }).addTo(map)
    drawLayerRef.current = L.layerGroup().addTo(map)

    map.on('click', (e) => {
      const s = stateRef.current
      if (s.closed) return
      // Near the starting point with a valid ring? Snap shut instead of
      // dropping a near-duplicate vertex.
      if (s.vertices.length >= 3) {
        const first = map.latLngToContainerPoint([s.vertices[0].lat, s.vertices[0].lon])
        const here = map.latLngToContainerPoint(e.latlng)
        if (first.distanceTo(here) <= CLOSE_SNAP_PX) {
          s.onClose()
          return
        }
      }
      s.onAddVertex({ lat: e.latlng.lat, lon: e.latlng.lng })
    })

    mapRef.current = map
    return () => map.remove()
  }, [])

  // Redraw the outline whenever the vertex list or closed state changes.
  useEffect(() => {
    const layer = drawLayerRef.current
    if (!layer) return
    layer.clearLayers()

    const latlngs = vertices.map((v) => [v.lat, v.lon])

    if (closed && latlngs.length >= 3) {
      L.polygon(latlngs, {
        color: GREEN,
        weight: 3,
        fillColor: GREEN_LIGHT,
        fillOpacity: 0.25,
      }).addTo(layer)
    } else if (latlngs.length >= 2) {
      L.polyline(latlngs, { color: GREEN_LIGHT, weight: 3, dashArray: '6 6' }).addTo(layer)
    }

    vertices.forEach((v, i) => {
      const isAnchor = i === 0 && !closed
      L.circleMarker([v.lat, v.lon], {
        // The first vertex is drawn bigger while drawing: it is the "click
        // here to close the field" target.
        radius: isAnchor ? 8 : 5,
        color: '#ffffff',
        weight: 2,
        fillColor: isAnchor ? GREEN : GREEN_LIGHT,
        fillOpacity: 1,
      }).addTo(layer)
    })
  }, [vertices, closed])

  return <div ref={containerRef} className="field-map" aria-label="Field boundary map" />
}

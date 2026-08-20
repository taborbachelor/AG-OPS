import { useEffect, useRef } from 'react'
import * as Cesium from 'cesium'
import 'cesium/Build/Cesium/Widgets/widgets.css'

import { cameraTarget, planPathPoints, GROUND_ALT_M } from './preview3d'

// The SAME imagery the drawing step and the 2D review map use, so the field a
// customer drew is recognisably the field they are now looking at. It is a
// plain tile URL: no Cesium Ion account, no access token, nothing to leak on a
// public site. The operator GCS uses this identical URL (MapView3D.jsx).
const ESRI_WORLD_IMAGERY =
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'

const FIELD_OUTLINE = Cesium.Color.fromCssColorString('#1a7f37')
const FIELD_FILL = Cesium.Color.fromCssColorString('#4cae4f').withAlpha(0.25)
const PATH_COLOR = Cesium.Color.fromCssColorString('#ffb703')

// Looking down at ~35 deg reads as "a drone flying over your field" while
// still showing the ground plane. Straight down would just be the 2D map
// again; near-horizontal would hide the field shape the customer drew.
const PITCH_DEG = -35

/**
 * Read-only 3D view of the ordered field and the spray path over it.
 *
 * This module is loaded LAZILY (see SprayPlanPreview.jsx). Cesium's runtime is
 * multiple megabytes, and a customer who never opens the 3D view must not pay
 * for it on a booking funnel where load time costs conversions.
 *
 * Props:
 *   vertices  [{lat, lon}] closed field boundary (>= 3 points)
 *   plan      the /api/coverage/plan response, or null/false while
 *             loading/failed — the field still renders without it.
 */
export default function FieldPreview3D({ vertices, plan }) {
  const containerRef = useRef(null)
  const viewerRef = useRef(null)

  // Build the viewer once per field. Cesium owns a WebGL context and a set of
  // web workers, so failing to destroy it leaks both — hence the explicit
  // teardown, guarded because destroy() throws if it already happened.
  useEffect(() => {
    const target = cameraTarget(vertices)
    if (!target) return undefined

    let viewer
    try {
      viewer = new Cesium.Viewer(containerRef.current, {
        animation: false, timeline: false, baseLayerPicker: false,
        geocoder: false, homeButton: false, sceneModePicker: false,
        navigationHelpButton: false, fullscreenButton: false,
        selectionIndicator: false, infoBox: false,
        baseLayer: new Cesium.ImageryLayer(
          new Cesium.UrlTemplateImageryProvider({
            url: ESRI_WORLD_IMAGERY, maximumLevel: 19,
          })),
      })
    } catch {
      // No WebGL (old machine, blocked GPU, headless browser). The 3D view is
      // decorative; the booking flow must not care. SprayPlanPreview keeps the
      // 2D map one click away, so there is always a working picture.
      return undefined
    }
    viewer.scene.globe.enableLighting = false
    viewer.resolutionScale = Math.min(window.devicePixelRatio || 1, 2)
    viewerRef.current = viewer

    viewer.entities.add({
      polygon: {
        hierarchy: new Cesium.PolygonHierarchy(
          Cesium.Cartesian3.fromDegreesArrayHeights(
            (vertices || []).flatMap((v) => [v.lon, v.lat, GROUND_ALT_M]))),
        material: FIELD_FILL,
        outline: true,
        outlineColor: FIELD_OUTLINE,
        outlineWidth: 3,
        perPositionHeight: true,
      },
    })

    viewer.camera.lookAt(
      Cesium.Cartesian3.fromDegrees(target.lon, target.lat, GROUND_ALT_M),
      new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(PITCH_DEG),
                                   target.range))
    // Release the lookAt transform so the user's own drag/zoom is unrestricted.
    viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY)

    return () => {
      viewerRef.current = null
      if (!viewer.isDestroyed()) viewer.destroy()
    }
  }, [vertices])

  // The path arrives after the viewer (a separate fetch), so it is its own
  // entity added on arrival and removed when the plan changes.
  useEffect(() => {
    const viewer = viewerRef.current
    if (!viewer || viewer.isDestroyed()) return undefined
    const { positions } = planPathPoints(plan)
    if (positions.length < 2) return undefined

    // Altitudes come from the waypoints themselves — see planPathPoints. The
    // line is drawn at the height the aircraft actually flies, not a height
    // chosen to look good from this camera angle.
    const entity = viewer.entities.add({
      polyline: {
        positions: Cesium.Cartesian3.fromDegreesArrayHeights(
          positions.flatMap(([la, lo, al]) => [lo, la, al])),
        width: 2.5,
        material: PATH_COLOR,
      },
    })
    return () => {
      if (!viewer.isDestroyed()) viewer.entities.remove(entity)
    }
  }, [plan])

  return (
    <div
      ref={containerRef}
      className="plan-map"
      aria-label="3D preview of your field and the spray path over it"
    />
  )
}

/**
 * MapView3D — the 3D flight view (CesiumJS).
 *
 * Same contract as the 2D MapView (a subset of its props), rendered as a real
 * 3D world: satellite imagery on the globe, the aircraft as a primitive-built
 * model that banks/pitches with live attitude at its real altitude, mission
 * and spray paths drawn in 3D space, the geofence as a translucent cylinder,
 * and chase/orbit/free cameras.
 *
 * Design constraints:
 *  - Key-free: imagery is the same Esri World Imagery tile URL the 2D map
 *    uses; terrain is the plain ellipsoid (the operating area is Kansas
 *    farmland — flat), so heights are simply relative-altitude above ground
 *    with the ground at 0. No Cesium ion token anywhere.
 *  - React renders ONE div. All scene updates are imperative: telemetry flows
 *    through a ref into Cesium CallbackProperties, so the 10Hz WS re-render
 *    of App costs nothing here (no reconciliation of scene objects).
 *  - Editing stays in 2D: this view is for flying/monitoring. Planning
 *    interactions (waypoint drag, field drawing) switch the app to MapView.
 */
import React, { useEffect, useRef, useState } from 'react';
import * as Cesium from 'cesium';
import 'cesium/Build/Cesium/Widgets/widgets.css';

const IMAGERY_URL =
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';

const CYAN = Cesium.Color.fromCssColorString('#00e5ff');
const GREEN = Cesium.Color.fromCssColorString('#00e676');
const ORANGE = Cesium.Color.fromCssColorString('#ff9100');
const PURPLE = Cesium.Color.fromCssColorString('#b388ff');
const RED = Cesium.Color.fromCssColorString('#ff5c5c');

const ZONE_COLOR = {
  water: Cesium.Color.fromCssColorString('#3b82f6'),
  trees: GREEN,
  buildings: ORANGE,
  holes: RED,
};

const LEG_STYLE = {
  spray: { color: CYAN, width: 3 },
  hop: { color: CYAN.withAlpha(0.35), width: 2 },
  transit: { color: ORANGE, width: 2.5 },
  home: { color: PURPLE, width: 2.5 },
};

// Aircraft primitive model, scaled up for visibility from mission distances.
// Local frame at heading 0 is north-west-up: +X = nose, +Y = left wing.
const FUSELAGE = new Cesium.Cartesian3(9, 1.4, 1.4);
const WING = new Cesium.Cartesian3(2.4, 14, 0.35);
const TAIL = new Cesium.Cartesian3(2.2, 5, 0.3);
const FIN = new Cesium.Cartesian3(2.0, 0.3, 2.2);

function cart(lat, lon, alt = 0) {
  return Cesium.Cartesian3.fromDegrees(lon, lat, Math.max(0, alt));
}

export default function MapView3D({
  telemetry, waypoints, fence, playbackPath,
  sprayFields, sprayLegs, zones, onSwitchTo2D,
}) {
  const containerRef = useRef(null);
  const viewerRef = useRef(null);
  // Latest props, readable from Cesium callbacks without re-creating them.
  const live = useRef({ telemetry, camMode: 'chase' });
  live.current.telemetry = telemetry;

  const [camMode, setCamMode] = useState('chase'); // chase | orbit | free
  live.current.camMode = camMode;

  // --- viewer lifecycle (mount once) ---
  useEffect(() => {
    const viewer = new Cesium.Viewer(containerRef.current, {
      animation: false, timeline: false, baseLayerPicker: false,
      geocoder: false, homeButton: false, sceneModePicker: false,
      navigationHelpButton: false, fullscreenButton: false,
      selectionIndicator: false, infoBox: false,
      baseLayer: new Cesium.ImageryLayer(
        new Cesium.UrlTemplateImageryProvider({ url: IMAGERY_URL, maximumLevel: 19 })),
      requestRenderMode: false,
    });
    viewer.scene.globe.baseColor = Cesium.Color.fromCssColorString('#06090f');
    viewer.scene.backgroundColor = Cesium.Color.fromCssColorString('#06090f');
    viewer.scene.globe.enableLighting = false;
    viewer.resolutionScale = Math.min(window.devicePixelRatio || 1, 2);
    viewerRef.current = viewer;

    // Aircraft: position/orientation live off the telemetry ref.
    const posProp = new Cesium.CallbackProperty(() => {
      const t = live.current.telemetry;
      if (!t || !t.lat) return undefined;
      return cart(t.lat, t.lon, t.altitude);
    }, false);
    const oriProp = new Cesium.CallbackProperty(() => {
      const t = live.current.telemetry;
      if (!t || !t.lat) return undefined;
      const hpr = new Cesium.HeadingPitchRoll(
        Cesium.Math.toRadians(t.heading || 0), -(t.pitch || 0), t.roll || 0);
      return Cesium.Transforms.headingPitchRollQuaternion(
        cart(t.lat, t.lon, t.altitude), hpr);
    }, false);

    const partColor = CYAN.withAlpha(0.95);
    const mkPart = (dims, name) => viewer.entities.add({
      name, position: posProp, orientation: oriProp,
      box: { dimensions: dims, material: partColor,
             outline: true, outlineColor: Cesium.Color.BLACK.withAlpha(0.4) },
    });
    mkPart(FUSELAGE, 'fuselage');
    mkPart(WING, 'wing');
    const tailOffset = mkPart(TAIL, 'tail');
    const finOffset = mkPart(FIN, 'fin');
    // Tail surfaces sit at the rear of the fuselage: model them as separate
    // boxes trailing the same pose (offset baked into the box via a model
    // matrix is not available on entities, so accept centered tail parts —
    // reads as a stylized aircraft, which is the goal).
    tailOffset.show = new Cesium.ConstantProperty(true);
    finOffset.show = new Cesium.ConstantProperty(true);

    // Ground reference: a faint drop-line + shadow dot make altitude legible.
    viewer.entities.add({
      polyline: {
        positions: new Cesium.CallbackProperty(() => {
          const t = live.current.telemetry;
          if (!t || !t.lat || !(t.altitude > 1)) return [];
          return [cart(t.lat, t.lon, t.altitude), cart(t.lat, t.lon, 0)];
        }, false),
        width: 1.5, material: CYAN.withAlpha(0.35),
      },
    });
    viewer.entities.add({
      position: new Cesium.CallbackProperty(() => {
        const t = live.current.telemetry;
        if (!t || !t.lat) return undefined;
        return cart(t.lat, t.lon, 0);
      }, false),
      point: { pixelSize: 5, color: CYAN.withAlpha(0.5) },
    });

    // Live trail ring (fed by the telemetry effect below).
    viewer._trail = [];
    viewer.entities.add({
      polyline: {
        positions: new Cesium.CallbackProperty(() => viewer._trail, false),
        width: 2.5, material: CYAN.withAlpha(0.8),
      },
    });

    // Chase camera: follow behind the aircraft along its heading.
    const chase = viewer.scene.preRender.addEventListener(() => {
      if (live.current.camMode !== 'chase') return;
      const t = live.current.telemetry;
      if (!t || !t.lat) return;
      viewer.camera.lookAt(
        cart(t.lat, t.lon, t.altitude),
        new Cesium.HeadingPitchRange(
          Cesium.Math.toRadians(t.heading || 0),
          Cesium.Math.toRadians(-18), 220));
    });

    // Initial camera: over the aircraft (or Kansas) looking down-tilted.
    const t0 = live.current.telemetry;
    viewer.camera.flyTo({
      destination: t0 && t0.lat
        ? cart(t0.lat, t0.lon, (t0.altitude || 0) + 600)
        : Cesium.Cartesian3.fromDegrees(-95.7997, 39.9042, 2500),
      orientation: { heading: 0, pitch: Cesium.Math.toRadians(-55), roll: 0 },
      duration: 0,
    });

    return () => {
      chase();
      viewer.destroy();
      viewerRef.current = null;
    };
  }, []);

  // --- trail accumulation (per telemetry frame, capped) ---
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !telemetry || !telemetry.lat) return;
    const trail = viewer._trail;
    const p = cart(telemetry.lat, telemetry.lon, telemetry.altitude);
    const last = trail[trail.length - 1];
    if (!last || Cesium.Cartesian3.distance(last, p) > 2) {
      trail.push(p);
      if (trail.length > 800) trail.shift();
    }
  }, [telemetry]);

  // --- camera mode side effects ---
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer) return;
    if (camMode !== 'chase') {
      viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);
    }
    if (camMode === 'orbit') {
      // Track the fuselage entity: Cesium's built-in orbit-follow.
      viewer.trackedEntity = viewer.entities.values.find((e) => e.name === 'fuselage');
    } else {
      viewer.trackedEntity = undefined;
    }
  }, [camMode]);

  // --- declarative overlays: rebuilt when their props change ---
  const overlayIds = useRef([]);
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer) return;
    for (const id of overlayIds.current) viewer.entities.removeById(id);
    overlayIds.current = [];
    const add = (opts) => {
      const e = viewer.entities.add(opts);
      overlayIds.current.push(e.id);
      return e;
    };

    const t = telemetry || {};
    const home = t.home_lat ? { lat: t.home_lat, lon: t.home_lon } : null;

    // Home marker.
    if (home) {
      add({
        position: cart(home.lat, home.lon, 0),
        point: { pixelSize: 10, color: GREEN, outlineColor: Cesium.Color.BLACK, outlineWidth: 1 },
        label: {
          text: 'H', font: '600 12px Inter, sans-serif',
          fillColor: Cesium.Color.WHITE, pixelOffset: new Cesium.Cartesian2(0, -16),
        },
      });
    }

    // Geofence: translucent cylinder (radius + altitude lid) around home.
    if (fence && fence.enable && home) {
      add({
        position: cart(home.lat, home.lon, (fence.alt_max || 120) / 2),
        cylinder: {
          length: fence.alt_max || 120,
          topRadius: fence.radius, bottomRadius: fence.radius,
          material: ORANGE.withAlpha(0.07),
          outline: true, outlineColor: ORANGE.withAlpha(0.5),
        },
      });
    }

    // Mission waypoints + connecting path at their real altitudes.
    const wps = (waypoints || []).filter((w) => w.command !== 'RTL');
    if (wps.length) {
      wps.forEach((w, i) => {
        add({
          position: cart(w.lat, w.lon, w.alt || 0),
          point: { pixelSize: 9, color: CYAN, outlineColor: Cesium.Color.BLACK, outlineWidth: 1 },
          label: {
            text: String(i + 1), font: '600 11px Inter, sans-serif',
            fillColor: Cesium.Color.WHITE, pixelOffset: new Cesium.Cartesian2(0, -14),
          },
        });
        add({
          polyline: {
            positions: [cart(w.lat, w.lon, w.alt || 0), cart(w.lat, w.lon, 0)],
            width: 1, material: CYAN.withAlpha(0.25),
          },
        });
      });
      add({
        polyline: {
          positions: wps.map((w) => cart(w.lat, w.lon, w.alt || 0)),
          width: 2.5, material: new Cesium.PolylineDashMaterialProperty({ color: CYAN }),
        },
      });
    }

    // Spray job: field polygons (with holes) on the ground.
    for (const f of sprayFields || []) {
      if (!f.polygon || f.polygon.length < 3) continue;
      add({
        polygon: {
          hierarchy: new Cesium.PolygonHierarchy(
            f.polygon.map((p) => cart(p.lat, p.lon, 0)),
            (f.holes || []).map((h) => new Cesium.PolygonHierarchy(
              h.map((p) => cart(p.lat, p.lon, 0))))),
          material: GREEN.withAlpha(0.12),
          outline: true, outlineColor: GREEN,
        },
      });
    }

    // No-spray zones.
    if (zones) {
      for (const [kind, polys] of Object.entries(zones)) {
        const color = ZONE_COLOR[kind];
        if (!color || !Array.isArray(polys)) continue;
        for (const poly of polys) {
          const ring = (poly.polygon || poly);
          if (!Array.isArray(ring) || ring.length < 3) continue;
          add({
            polygon: {
              hierarchy: new Cesium.PolygonHierarchy(
                ring.map((p) => cart(p.lat, p.lon, 0))),
              material: color.withAlpha(0.25),
              outline: true, outlineColor: color,
            },
          });
        }
      }
    }

    // Planned flight legs at altitude ([lat, lon, alt?] point lists).
    for (const leg of sprayLegs || []) {
      const style = LEG_STYLE[leg.kind] || LEG_STYLE.spray;
      add({
        polyline: {
          positions: leg.pts.map(([la, lo, al]) => cart(la, lo, al ?? 60)),
          width: style.width, material: style.color,
        },
      });
    }

    // Recorded playback track at its real altitudes.
    if (playbackPath && playbackPath.length > 1) {
      add({
        polyline: {
          positions: playbackPath.map(([la, lo, al]) => cart(la, lo, al || 0)),
          width: 3, material: PURPLE,
        },
      });
    }
  }, [waypoints, fence, sprayFields, sprayLegs, zones, playbackPath,
      telemetry && telemetry.home_lat]);

  const centerOnAircraft = () => {
    const viewer = viewerRef.current;
    const t = live.current.telemetry;
    if (!viewer || !t || !t.lat) return;
    setCamMode('free');
    viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);
    viewer.camera.flyTo({
      destination: cart(t.lat, t.lon, (t.altitude || 0) + 500),
      orientation: { heading: 0, pitch: Cesium.Math.toRadians(-60), roll: 0 },
      duration: 0.8,
    });
  };

  return (
    <div className="map3d-wrap">
      <div ref={containerRef} className="map3d-container" />
      <div className="map-controls map3d-controls">
        <button
          className={`map-ctrl-btn wide ${camMode === 'chase' ? 'active' : ''}`}
          onClick={() => setCamMode('chase')}
          title="Chase camera — behind the aircraft"
        >CHASE</button>
        <button
          className={`map-ctrl-btn wide ${camMode === 'orbit' ? 'active' : ''}`}
          onClick={() => setCamMode('orbit')}
          title="Orbit camera — drag to circle the aircraft"
        >ORBIT</button>
        <button
          className={`map-ctrl-btn wide ${camMode === 'free' ? 'active' : ''}`}
          onClick={centerOnAircraft}
          title="Free camera — centered above the aircraft"
        >FREE</button>
        <button className="map-ctrl-btn wide" onClick={onSwitchTo2D} title="Flat map">2D</button>
      </div>
    </div>
  );
}

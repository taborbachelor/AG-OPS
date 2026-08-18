import React, { useEffect, useRef, useState } from 'react';
import { MapContainer, TileLayer, Marker, Polyline, Polygon, Circle, CircleMarker, useMap, useMapEvents } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

// --- Basemap layers (all free, no API key) ---
const LAYERS = {
  sat: {
    label: 'SAT',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Tiles &copy; Esri',
  },
  map: {
    label: 'MAP',
    url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '&copy; OpenStreetMap',
  },
  hybrid: {
    label: 'HYB',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Tiles &copy; Esri',
    overlay: 'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
  },
};

// Aircraft icon: a plane silhouette that rotates to the current heading.
function aircraftIcon(heading) {
  return L.divIcon({
    html: `<div style="transform: rotate(${heading}deg); transition: transform 0.3s ease;
      filter: drop-shadow(0 0 6px rgba(0,229,255,0.8));">
      <svg viewBox="0 0 24 24" width="34" height="34">
        <path d="M12 1 L13.2 9 L23 15 L23 17 L13.2 13.5 L13.2 20 L16 22 L16 23 L12 21.4 L8 23 L8 22 L10.8 20 L10.8 13.5 L1 17 L1 15 L10.8 9 Z"
          fill="#00e5ff" stroke="#04121f" stroke-width="0.6" stroke-linejoin="round"/>
      </svg>
    </div>`,
    iconSize: [34, 34],
    iconAnchor: [17, 17],
    className: '',
  });
}

const homeIcon = L.divIcon({
  html: `<div style="
    width: 22px; height: 22px; background: rgba(0,230,118,0.9);
    border: 2px solid #fff; border-radius: 4px;
    display:flex; align-items:center; justify-content:center;
    color:#04121f; font-weight:800; font-size:12px;
    font-family:'Inter',system-ui,sans-serif; box-shadow:0 0 8px rgba(0,230,118,0.6);">H</div>`,
  iconSize: [22, 22],
  iconAnchor: [11, 11],
  className: '',
});

const CMD_COLOR = {
  TAKEOFF: '#00e676', WAYPOINT: '#00e5ff', LOITER: '#ff9100', LAND: '#ff1744', RTL: '#b388ff',
};

// No-spray zone tints: water blue, trees green, buildings orange, and
// detected in-field holes (farmsteads/ponds) red. Powerlines are hazard
// yellow ON PURPOSE — every other zone protects spray quality, a powerline
// protects the airframe, so it must not read as another soft tint.
const ZONE_COLOR = {
  water: '#3b82f6', trees: '#00e676', buildings: '#ff9100',
  holes: '#ff5c5c', powerline: '#ffd600',
};

// Linear features (waterway ditches/streams, power lines) come back as
// ZERO-AREA corridor rings — the line traced out and back — so a fill
// renders nothing and a 1px stroke is nearly invisible on imagery. Draw
// them as real strokes instead, or the operator cannot see the keepout the
// planner just routed around.
const isCorridor = (z) => !!(z && z.tags && (z.tags.waterway || z.tags.power));

const zoneStyle = (kind, z) => (isCorridor(z)
  ? { color: ZONE_COLOR[kind], weight: 3, opacity: 0.95,
      dashArray: kind === 'powerline' ? '10 5' : undefined, fillOpacity: 0 }
  : { color: ZONE_COLOR[kind], weight: 1, opacity: 0.7,
      fillColor: ZONE_COLOR[kind], fillOpacity: 0.25 });

// Whole-job flight legs: spray-on solid, in-field hops faint, inter-field
// transit orange, home legs purple — the operator sees the ENTIRE flight.
const LEG_STYLE = {
  spray: { color: '#00e5ff', weight: 3, opacity: 0.95 },
  hop: { color: '#00e5ff', weight: 1.5, opacity: 0.45, dashArray: '3 5' },
  transit: { color: '#ff9100', weight: 2.5, opacity: 0.85, dashArray: '8 6' },
  home: { color: '#b388ff', weight: 2, opacity: 0.8, dashArray: '8 6' },
};

function fieldBadge(n) {
  return L.divIcon({
    html: `<div style="width:20px;height:20px;border-radius:50%;background:#00e676;
      border:2px solid #fff;color:#04121f;font:800 11px 'Inter',system-ui,sans-serif;
      display:flex;align-items:center;justify-content:center;
      box-shadow:0 0 8px rgba(0,230,118,.6)">${n}</div>`,
    iconSize: [20, 20],
    iconAnchor: [10, 10],
    className: '',
  });
}

function waypointIcon(seq, command, draggable) {
  const color = CMD_COLOR[command] || '#00e5ff';
  return L.divIcon({
    html: `<div style="
      width: 26px; height: 26px; background: ${color};
      border: 2px solid rgba(255,255,255,0.85);
      border-radius: 50% 50% 50% 0; transform: rotate(-45deg);
      box-shadow: 0 0 10px ${color}99;
      display: flex; align-items: center; justify-content: center;
      cursor: ${draggable ? 'grab' : 'default'};">
      <span style="transform: rotate(45deg); color: #04121f; font-weight: 800;
        font-size: 12px; font-family: 'Inter', system-ui, sans-serif;">${seq}</span>
    </div>`,
    iconSize: [26, 26],
    iconAnchor: [13, 26],
    className: '',
  });
}

function InitialCenter({ lat, lon }) {
  const map = useMap();
  const done = useRef(false);
  useEffect(() => {
    if (lat !== 0 && lon !== 0 && !done.current) {
      map.setView([lat, lon], 17);
      done.current = true;
    }
  }, [lat, lon, map]);
  return null;
}

function Follow({ lat, lon, enabled }) {
  const map = useMap();
  useEffect(() => {
    if (enabled && lat !== 0 && lon !== 0) {
      map.panTo([lat, lon], { animate: true, duration: 0.4 });
    }
  }, [lat, lon, enabled, map]);
  return null;
}

function ClickHandler({ enabled, onAddWaypoint }) {
  useMapEvents({
    click(e) { if (enabled) onAddWaypoint(e.latlng); },
  });
  return null;
}

// react-leaflet v5 freezes MapContainer's className/style props at first
// render (they're captured in a useState), so the layer tint class and the
// draw-mode crosshair cursor must be kept in sync on the live container
// element instead.
function ContainerSync({ layer, crosshair }) {
  const map = useMap();
  useEffect(() => {
    const el = map.getContainer();
    Object.keys(LAYERS).forEach((k) => el.classList.remove(`layer-${k}`));
    el.classList.add(`layer-${layer}`);
    el.style.cursor = crosshair ? 'crosshair' : 'grab';
  }, [map, layer, crosshair]);
  return null;
}

// Frame a newly set spray field (e.g. loaded from an order) once per change.
// While drawing, the array changes on every click — skip those so the map
// stays put under the operator's cursor.
function FitField({ field, drawing }) {
  const map = useMap();
  const prev = useRef(field);
  useEffect(() => {
    const changed = field !== prev.current;
    prev.current = field;
    if (!changed || drawing || field.length < 3) return;
    map.fitBounds(field.map((p) => [p.lat, p.lon]), { padding: [60, 60] });
  }, [field, drawing, map]);
  return null;
}

function MapControls({ layer, setLayer, follow, setFollow, onCenter }) {
  return (
    <div className="map-controls">
      <div className="layer-switch">
        {Object.entries(LAYERS).map(([key, l]) => (
          <button
            key={key}
            className={`map-ctrl-btn ${layer === key ? 'active' : ''}`}
            onClick={() => setLayer(key)}
          >
            {l.label}
          </button>
        ))}
      </div>
      <button
        className={`map-ctrl-btn wide ${follow ? 'active' : ''}`}
        onClick={() => setFollow((f) => !f)}
      >
        {follow ? '◉ FOLLOW' : '○ FOLLOW'}
      </button>
      <button className="map-ctrl-btn wide" onClick={onCenter}>⌖ CENTER</button>
    </div>
  );
}

// Frame the whole job when a field is added/removed (never mid-draw).
function FitJob({ fields, area, drawing }) {
  const map = useMap();
  const prevCount = useRef(0);
  useEffect(() => {
    const count = fields.length;
    if (count !== prevCount.current && !drawing) {
      const pts = [...fields.flatMap((f) => f.polygon || f), ...area]
        .map((p) => [p.lat, p.lon]);
      if (pts.length >= 3) map.fitBounds(pts, { padding: [60, 60] });
    }
    prevCount.current = count;
  }, [fields, area, drawing, map]);
  return null;
}

function MapView({
  telemetry, planning, waypoints = [], onAddWaypoint, onMoveWaypoint, fence, playbackPath,
  sprayField = [], sprayFields = [], sprayArea = [], sprayDrawing, onAddSprayVertex,
  sprayLegs = [], zones,
}) {
  const [layer, setLayer] = useState('sat');
  const [follow, setFollow] = useState(true);
  const trailRef = useRef([]);
  const homeRef = useRef(null);
  const mapRef = useRef(null);

  const hasFix = telemetry.lat !== 0 && telemetry.lon !== 0;
  const playback = !!playbackPath;

  // Launch-point fallback: latch the first LIVE fix only — log-playback
  // telemetry must never become "home" for a later live session — and clear
  // the latch when live telemetry ends so the next session re-latches fresh.
  if (hasFix && !playback && !homeRef.current) {
    homeRef.current = [telemetry.lat, telemetry.lon];
  }
  if (!hasFix && !playback && homeRef.current) {
    homeRef.current = null;
  }

  // Prefer the autopilot's real home position whenever telemetry carries one;
  // the first-fix latch is only a fallback until HOME_POSITION arrives.
  const homePos = (telemetry.home_lat || telemetry.home_lon)
    ? [telemetry.home_lat, telemetry.home_lon]
    : homeRef.current;

  // Append to the travelled trail (live only — during playback we draw the
  // full recorded path instead).
  if (hasFix && !playbackPath) {
    const trail = trailRef.current;
    const last = trail[trail.length - 1];
    if (!last || last[0] !== telemetry.lat || last[1] !== telemetry.lon) {
      trail.push([telemetry.lat, telemetry.lon]);
      if (trail.length > 500) trail.shift();
    }
  }

  const center = hasFix ? [telemetry.lat, telemetry.lon] : [39.8283, -98.5795];
  const active = LAYERS[layer];

  const positioned = waypoints.filter((w) => w.command !== 'RTL');
  const pathLine = positioned.map((w) => [w.lat, w.lon]);

  const centerOnAircraft = () => {
    if (mapRef.current && hasFix) mapRef.current.setView([telemetry.lat, telemetry.lon], 17);
  };

  return (
    <>
      <MapContainer
        center={center}
        zoom={4}
        className={`layer-${layer}`}
        style={{ height: '100%', width: '100%', background: '#0a0e17',
                 cursor: planning || sprayDrawing ? 'crosshair' : 'grab' }}
        zoomControl={false}
        ref={mapRef}
      >
        <TileLayer key={layer} url={active.url} attribution={active.attribution} maxZoom={19} />
        {active.overlay && <TileLayer key={`${layer}-ov`} url={active.overlay} maxZoom={19} />}

        <ContainerSync layer={layer} crosshair={!!(planning || sprayDrawing)} />
        <InitialCenter lat={telemetry.lat} lon={telemetry.lon} />
        <Follow lat={telemetry.lat} lon={telemetry.lon}
          enabled={follow && !planning && !sprayDrawing} />
        <ClickHandler enabled={planning} onAddWaypoint={onAddWaypoint} />
        {/* Spray-field drawing: App.js guarantees only one of planning /
            sprayDrawing is ever active, so the handlers cannot conflict. */}
        <ClickHandler enabled={sprayDrawing} onAddWaypoint={onAddSprayVertex} />
        <FitField field={sprayField} drawing={sprayDrawing} />
        <FitJob fields={sprayFields} area={sprayArea} drawing={sprayDrawing} />

        {/* No-spray zones (semi-transparent tints under everything else) */}
        {zones && ['water', 'trees', 'buildings', 'holes', 'powerline'].map((kind) =>
          (zones[kind] || []).map((z, i) => (
            <Polygon
              key={`${kind}-${i}`}
              positions={z.coords.map((c) => [c.lat, c.lon])}
              pathOptions={zoneStyle(kind, z)}
            />
          )))}

        {/* Spray field boundary — green dashed, distinct from mission paths */}
        {sprayField.length >= 3 && (
          <Polygon
            positions={sprayField.map((p) => [p.lat, p.lon])}
            pathOptions={{ color: '#00e676', weight: 2, dashArray: '6 6',
              fillColor: '#00e676', fillOpacity: 0.06 }}
          />
        )}
        {sprayField.length === 2 && (
          <Polyline
            positions={sprayField.map((p) => [p.lat, p.lon])}
            pathOptions={{ color: '#00e676', weight: 2, dashArray: '6 6' }}
          />
        )}
        {sprayDrawing && sprayField.map((p, i) => (
          <CircleMarker
            key={`fv-${i}`}
            center={[p.lat, p.lon]}
            radius={4}
            pathOptions={{ color: '#fff', weight: 1, fillColor: '#00e676', fillOpacity: 1 }}
          />
        ))}

        {/* Selection area for auto-detect — white dashed */}
        {sprayArea.length >= 2 && (
          <Polygon
            positions={sprayArea.map((p) => [p.lat, p.lon])}
            pathOptions={{ color: '#ffffff', weight: 2, dashArray: '10 8',
              opacity: 0.7, fillColor: '#ffffff', fillOpacity: 0.04 }}
          />
        )}

        {/* Committed job fields — numbered; holes render as true cut-outs */}
        {sprayFields.map((f, i) => f.polygon.length >= 3 && (
          <React.Fragment key={`jf-${i}`}>
            <Polygon
              positions={[
                f.polygon.map((p) => [p.lat, p.lon]),
                ...(f.holes || []).map((h) => h.map((p) => [p.lat, p.lon])),
              ]}
              pathOptions={{ color: '#00e676', weight: 2,
                fillColor: '#00e676', fillOpacity: 0.08 }}
            />
            <Marker
              position={[
                f.polygon.reduce((s, p) => s + p.lat, 0) / f.polygon.length,
                f.polygon.reduce((s, p) => s + p.lon, 0) / f.polygon.length,
              ]}
              icon={fieldBadge(i + 1)}
            />
          </React.Fragment>
        ))}

        {/* Whole-job flight: spray passes, hops, transits, home legs */}
        {sprayLegs.map((leg, i) => (
          <Polyline key={`leg-${i}`} positions={leg.pts}
            pathOptions={LEG_STYLE[leg.kind] || LEG_STYLE.spray} />
        ))}

        {/* Planned flight path */}
        {pathLine.length > 1 && (
          <Polyline positions={pathLine} color="#00e5ff" weight={2} opacity={0.85} dashArray="6 6" />
        )}
        {positioned.map((w, idx) => (
          <Marker
            key={w.id}
            position={[w.lat, w.lon]}
            icon={waypointIcon(idx + 1, w.command, planning)}
            draggable={planning}
            eventHandlers={{ dragend: (e) => onMoveWaypoint(w.id, e.target.getLatLng()) }}
          />
        ))}

        {/* Geofence (circular) centered on home */}
        {fence && fence.enable && fence.radius > 0 && homePos && (
          <Circle
            center={homePos}
            radius={fence.radius}
            pathOptions={{ color: '#ff9100', weight: 2, opacity: 0.8,
              fillColor: '#ff9100', fillOpacity: 0.06, dashArray: '4 6' }}
          />
        )}

        {/* Home / launch point */}
        {homePos && <Marker position={homePos} icon={homeIcon} />}

        {/* Recorded flight path during playback */}
        {playbackPath && playbackPath.length > 1 && (
          <Polyline positions={playbackPath} color="#b388ff" weight={3} opacity={0.8} />
        )}

        {/* Travelled trail + live aircraft */}
        {!playbackPath && trailRef.current.length > 1 && (
          <Polyline positions={trailRef.current} color="#00e5ff" weight={2} opacity={0.6} />
        )}
        {hasFix && (
          <Marker position={[telemetry.lat, telemetry.lon]} icon={aircraftIcon(telemetry.heading)} />
        )}
      </MapContainer>

      <MapControls
        layer={layer}
        setLayer={setLayer}
        follow={follow}
        setFollow={setFollow}
        onCenter={centerOnAircraft}
      />
    </>
  );
}

export default MapView;

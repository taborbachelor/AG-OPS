import React, { useEffect, useRef, useState } from 'react';
import { MapContainer, TileLayer, Marker, Polyline, Circle, useMap, useMapEvents } from 'react-leaflet';
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

function MapView({ telemetry, planning, waypoints = [], onAddWaypoint, onMoveWaypoint, fence, playbackPath }) {
  const [layer, setLayer] = useState('sat');
  const [follow, setFollow] = useState(true);
  const trailRef = useRef([]);
  const homeRef = useRef(null);
  const mapRef = useRef(null);

  const hasFix = telemetry.lat !== 0 && telemetry.lon !== 0;

  // Capture the launch point the first time we get a position.
  if (hasFix && !homeRef.current) {
    homeRef.current = [telemetry.lat, telemetry.lon];
  }

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
                 cursor: planning ? 'crosshair' : 'grab' }}
        zoomControl={false}
        ref={mapRef}
      >
        <TileLayer key={layer} url={active.url} attribution={active.attribution} maxZoom={19} />
        {active.overlay && <TileLayer key={`${layer}-ov`} url={active.overlay} maxZoom={19} />}

        <InitialCenter lat={telemetry.lat} lon={telemetry.lon} />
        <Follow lat={telemetry.lat} lon={telemetry.lon} enabled={follow && !planning} />
        <ClickHandler enabled={planning} onAddWaypoint={onAddWaypoint} />

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
        {fence && fence.enable && fence.radius > 0 && homeRef.current && (
          <Circle
            center={homeRef.current}
            radius={fence.radius}
            pathOptions={{ color: '#ff9100', weight: 2, opacity: 0.8,
              fillColor: '#ff9100', fillOpacity: 0.06, dashArray: '4 6' }}
          />
        )}

        {/* Home / launch point */}
        {homeRef.current && <Marker position={homeRef.current} icon={homeIcon} />}

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

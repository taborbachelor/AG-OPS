import React, { useEffect, useRef } from 'react';
import { MapContainer, TileLayer, Marker, Polyline, useMap, useMapEvents } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

// Plane icon - glowing cyan dot
const planeIcon = L.divIcon({
  html: `<div style="
    width: 16px; height: 16px;
    background: #00e5ff;
    border: 2px solid rgba(0,229,255,0.4);
    border-radius: 50%;
    box-shadow: 0 0 12px rgba(0,229,255,0.6), 0 0 24px rgba(0,229,255,0.3);
  "></div>`,
  iconSize: [16, 16],
  iconAnchor: [8, 8],
  className: '',
});

// Colored, numbered waypoint marker. Color hints the command type.
const CMD_COLOR = {
  TAKEOFF: '#00e676',
  WAYPOINT: '#00e5ff',
  LOITER: '#ff9100',
  LAND: '#ff1744',
  RTL: '#b388ff',
};

function waypointIcon(seq, command, draggable) {
  const color = CMD_COLOR[command] || '#00e5ff';
  return L.divIcon({
    html: `<div style="
      width: 26px; height: 26px;
      background: ${color};
      border: 2px solid rgba(255,255,255,0.85);
      border-radius: 50% 50% 50% 0;
      transform: rotate(-45deg);
      box-shadow: 0 0 10px ${color}99;
      display: flex; align-items: center; justify-content: center;
      cursor: ${draggable ? 'grab' : 'default'};
    ">
      <span style="
        transform: rotate(45deg);
        color: #04121f; font-weight: 700; font-size: 12px;
        font-family: 'Orbitron', monospace;
      ">${seq}</span>
    </div>`,
    iconSize: [26, 26],
    iconAnchor: [13, 26],
    className: '',
  });
}

function MapUpdater({ lat, lon }) {
  const map = useMap();
  const initialized = useRef(false);

  useEffect(() => {
    if (lat !== 0 && lon !== 0 && !initialized.current) {
      map.setView([lat, lon], 16);
      initialized.current = true;
    }
  }, [lat, lon, map]);

  return null;
}

function ClickHandler({ enabled, onAddWaypoint }) {
  useMapEvents({
    click(e) {
      if (enabled) onAddWaypoint(e.latlng);
    },
  });
  return null;
}

function MapView({ telemetry, planning, waypoints = [], onAddWaypoint, onMoveWaypoint }) {
  const trailRef = useRef([]);

  if (telemetry.lat !== 0 && telemetry.lon !== 0) {
    const trail = trailRef.current;
    const last = trail[trail.length - 1];
    if (!last || last[0] !== telemetry.lat || last[1] !== telemetry.lon) {
      trail.push([telemetry.lat, telemetry.lon]);
      if (trail.length > 500) trail.shift();
    }
  }

  const center = telemetry.lat !== 0 ? [telemetry.lat, telemetry.lon] : [39.8283, -98.5795];

  // Positioned waypoints (RTL has no location) form the drawn flight path.
  const positioned = waypoints.filter((w) => w.command !== 'RTL');
  const pathLine = positioned.map((w) => [w.lat, w.lon]);

  return (
    <MapContainer
      center={center}
      zoom={4}
      style={{ height: '100%', width: '100%', background: '#0a0e17',
               cursor: planning ? 'crosshair' : 'grab' }}
      zoomControl={false}
    >
      <TileLayer
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        attribution='&copy; OpenStreetMap'
      />
      <MapUpdater lat={telemetry.lat} lon={telemetry.lon} />
      <ClickHandler enabled={planning} onAddWaypoint={onAddWaypoint} />

      {/* Planned flight path */}
      {pathLine.length > 1 && (
        <Polyline positions={pathLine} color="#00e5ff" weight={2} opacity={0.8} dashArray="6 6" />
      )}

      {/* Waypoint markers */}
      {positioned.map((w, idx) => (
        <Marker
          key={w.id}
          position={[w.lat, w.lon]}
          icon={waypointIcon(idx + 1, w.command, planning)}
          draggable={planning}
          eventHandlers={{
            dragend: (e) => onMoveWaypoint(w.id, e.target.getLatLng()),
          }}
        />
      ))}

      {/* Live aircraft position + travelled trail */}
      {telemetry.lat !== 0 && (
        <Marker position={[telemetry.lat, telemetry.lon]} icon={planeIcon} />
      )}
      {trailRef.current.length > 1 && (
        <Polyline positions={trailRef.current} color="#00e5ff" weight={2} opacity={0.5} />
      )}
    </MapContainer>
  );
}

export default MapView;

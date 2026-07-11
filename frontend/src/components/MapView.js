import React, { useEffect, useRef } from 'react';
import { MapContainer, TileLayer, Marker, Polyline, useMap } from 'react-leaflet';
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

function MapView({ telemetry }) {
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

  return (
    <MapContainer
      center={center}
      zoom={4}
      style={{ height: '100%', width: '100%', background: '#0a0e17' }}
      zoomControl={false}
    >
      <TileLayer
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        attribution='&copy; OpenStreetMap'
      />
      <MapUpdater lat={telemetry.lat} lon={telemetry.lon} />

      {telemetry.lat !== 0 && (
        <Marker position={[telemetry.lat, telemetry.lon]} icon={planeIcon} />
      )}

      {trailRef.current.length > 1 && (
        <Polyline
          positions={trailRef.current}
          color="#00e5ff"
          weight={2}
          opacity={0.5}
        />
      )}
    </MapContainer>
  );
}

export default MapView;

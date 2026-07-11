import React, { useEffect, useRef } from 'react';
import { MapContainer, TileLayer, Marker, Polyline, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

// Fix default marker icon issue with webpack
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon-2x.png',
  iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-shadow.png',
});

const planeIcon = L.divIcon({
  html: `<div style="
    width: 20px; height: 20px; background: #3b82f6; border: 2px solid white;
    border-radius: 50%; box-shadow: 0 0 8px rgba(59,130,246,0.6);
  "></div>`,
  iconSize: [20, 20],
  iconAnchor: [10, 10],
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
    <MapContainer center={center} zoom={4} style={{ height: '100%', width: '100%' }}>
      <TileLayer
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        attribution='&copy; OpenStreetMap contributors'
      />
      <MapUpdater lat={telemetry.lat} lon={telemetry.lon} />

      {telemetry.lat !== 0 && (
        <Marker position={[telemetry.lat, telemetry.lon]} icon={planeIcon} />
      )}

      {trailRef.current.length > 1 && (
        <Polyline positions={trailRef.current} color="#3b82f6" weight={2} opacity={0.7} />
      )}
    </MapContainer>
  );
}

export default MapView;

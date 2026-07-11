import React, { useState, useEffect, useRef } from 'react';
import TopBar from './components/TopBar';
import HudLeft from './components/HudLeft';
import HudRight from './components/HudRight';
import HudBottom from './components/HudBottom';
import MapView from './components/MapView';
import VideoFeed from './components/VideoFeed';
import ConnectionOverlay from './components/ConnectionOverlay';
import MissionPanel from './components/MissionPanel';
import LaunchControl from './components/LaunchControl';
import './App.css';

const DEFAULT_TELEMETRY = {
  connected: false, armed: false, mode: 'UNKNOWN',
  altitude: 0, airspeed: 0, groundspeed: 0, heading: 0,
  lat: 0, lon: 0, battery_voltage: 0, battery_current: 0,
  battery_level: null, pitch: 0, roll: 0, yaw: 0,
  gps_fix: 0, gps_satellites: 0,
};

function App() {
  const [telemetry, setTelemetry] = useState(DEFAULT_TELEMETRY);
  const [connected, setConnected] = useState(false);
  const [showConnect, setShowConnect] = useState(false);
  const wsRef = useRef(null);

  // Mission planning state
  const [planning, setPlanning] = useState(false);
  const [waypoints, setWaypoints] = useState([]); // {id, command, lat, lon, alt}
  const [defaultAlt, setDefaultAlt] = useState(100);
  const nextId = useRef(1);

  // On load, adopt an existing backend connection (e.g. SITL already linked,
  // or the page was refreshed mid-flight) so the dashboard wakes up on its own.
  useEffect(() => {
    fetch('http://localhost:8000/api/connection/status')
      .then((r) => r.json())
      .then((s) => { if (s.connected) setConnected(true); })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!connected) return;

    const ws = new WebSocket('ws://localhost:8000/api/telemetry/ws');
    wsRef.current = ws;

    ws.onmessage = (event) => {
      setTelemetry(JSON.parse(event.data));
    };

    ws.onclose = () => setConnected(false);
    return () => ws.close();
  }, [connected]);

  // --- Mission editing handlers ---
  const addWaypoint = (latlng) => {
    setWaypoints((w) => [
      ...w,
      {
        id: nextId.current++,
        // First point defaults to TAKEOFF, the rest to WAYPOINT.
        command: w.length === 0 ? 'TAKEOFF' : 'WAYPOINT',
        lat: latlng.lat,
        lon: latlng.lng,
        alt: defaultAlt,
      },
    ]);
  };

  const moveWaypoint = (id, latlng) => {
    setWaypoints((w) => w.map((p) =>
      p.id === id ? { ...p, lat: latlng.lat, lon: latlng.lng } : p));
  };

  const updateWaypoint = (id, changes) => {
    setWaypoints((w) => w.map((p) => (p.id === id ? { ...p, ...changes } : p)));
  };

  const removeWaypoint = (id) => {
    setWaypoints((w) => w.filter((p) => p.id !== id));
  };

  const clearMission = () => setWaypoints([]);

  return (
    <div className="app">
      {/* Full-screen map background */}
      <div className="fullscreen-map">
        <MapView
          telemetry={telemetry}
          planning={planning}
          waypoints={waypoints}
          onAddWaypoint={addWaypoint}
          onMoveWaypoint={moveWaypoint}
        />
      </div>

      {/* HUD Overlays */}
      <TopBar
        telemetry={telemetry}
        connected={connected}
        planning={planning}
        onPlanClick={() => setPlanning((p) => !p)}
        onConnectClick={() => setShowConnect(!showConnect)}
      />

      {/* Left side: attitude/gauges when flying, mission editor when planning */}
      {planning ? (
        <MissionPanel
          connected={connected}
          waypoints={waypoints}
          setWaypoints={setWaypoints}
          defaultAlt={defaultAlt}
          setDefaultAlt={setDefaultAlt}
          updateWaypoint={updateWaypoint}
          removeWaypoint={removeWaypoint}
          clearMission={clearMission}
          nextId={nextId}
        />
      ) : (
        <HudLeft telemetry={telemetry} />
      )}

      <HudRight telemetry={telemetry} connected={connected} />
      <HudBottom telemetry={telemetry} />

      {/* Arm + takeoff flow (hidden while planning to keep the map clear) */}
      {!planning && <LaunchControl telemetry={telemetry} connected={connected} />}

      {/* Video Picture-in-Picture */}
      <VideoFeed />

      {/* Connection overlay */}
      {showConnect && (
        <ConnectionOverlay
          connected={connected}
          setConnected={setConnected}
          onClose={() => setShowConnect(false)}
        />
      )}
    </div>
  );
}

export default App;

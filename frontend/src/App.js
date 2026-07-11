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
  const [backendUp, setBackendUp] = useState(true);
  const [showConnect, setShowConnect] = useState(false);

  // Mission planning state
  const [planning, setPlanning] = useState(false);
  const [waypoints, setWaypoints] = useState([]); // {id, command, lat, lon, alt}
  const [defaultAlt, setDefaultAlt] = useState(100);
  const nextId = useRef(1);

  // Poll the backend for the true link state every 3s. This is the single
  // source of truth for `connected`, so the UI self-heals: it reflects link
  // loss (SITL exit, radio dropout), backend restarts, and page refreshes
  // without the user having to do anything.
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const r = await fetch('http://localhost:8000/api/connection/status');
        const s = await r.json();
        if (!alive) return;
        setBackendUp(true);
        setConnected(s.connected);
      } catch {
        if (!alive) return;
        setBackendUp(false);
        setConnected(false);
      }
    };
    poll();
    const id = setInterval(poll, 3000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  // Telemetry WebSocket, opened whenever we're connected and auto-reconnected
  // if it drops while still connected.
  useEffect(() => {
    if (!connected) {
      setTelemetry(DEFAULT_TELEMETRY);
      return;
    }
    let closedByUs = false;
    let ws;
    let retry;
    const open = () => {
      ws = new WebSocket('ws://localhost:8000/api/telemetry/ws');
      ws.onmessage = (event) => setTelemetry(JSON.parse(event.data));
      ws.onclose = () => {
        if (!closedByUs) retry = setTimeout(open, 1000);
      };
    };
    open();
    return () => {
      closedByUs = true;
      clearTimeout(retry);
      if (ws) ws.close();
    };
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
        backendUp={backendUp}
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

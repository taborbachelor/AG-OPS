import React, { useState, useEffect, useRef } from 'react';
import TopBar from './components/TopBar';
import HudLeft from './components/HudLeft';
import HudRight from './components/HudRight';
import HudBottom from './components/HudBottom';
import MapView from './components/MapView';
import VideoFeed from './components/VideoFeed';
import ConnectionOverlay from './components/ConnectionOverlay';
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

  return (
    <div className="app">
      {/* Full-screen map background */}
      <div className="fullscreen-map">
        <MapView telemetry={telemetry} />
      </div>

      {/* HUD Overlays */}
      <TopBar
        telemetry={telemetry}
        connected={connected}
        onConnectClick={() => setShowConnect(!showConnect)}
      />
      <HudLeft telemetry={telemetry} />
      <HudRight telemetry={telemetry} connected={connected} />
      <HudBottom telemetry={telemetry} />

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

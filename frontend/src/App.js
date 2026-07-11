import React, { useState, useEffect, useRef } from 'react';
import ConnectionPanel from './components/ConnectionPanel';
import TelemetryDashboard from './components/TelemetryDashboard';
import MapView from './components/MapView';
import FlightControls from './components/FlightControls';
import VideoFeed from './components/VideoFeed';
import './App.css';

function App() {
  const [telemetry, setTelemetry] = useState({
    connected: false, armed: false, mode: 'UNKNOWN',
    altitude: 0, airspeed: 0, groundspeed: 0, heading: 0,
    lat: 0, lon: 0, battery_voltage: 0, battery_current: 0,
    battery_level: null, pitch: 0, roll: 0, yaw: 0,
    gps_fix: 0, gps_satellites: 0,
  });
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);

  useEffect(() => {
    if (!connected) return;

    const ws = new WebSocket('ws://localhost:8000/api/telemetry/ws');
    wsRef.current = ws;

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      setTelemetry(data);
    };

    ws.onclose = () => {
      setConnected(false);
    };

    return () => ws.close();
  }, [connected]);

  return (
    <div className="app">
      <header className="app-header">
        <h1>RC Plane GCS</h1>
        <div className={`status-indicator ${connected ? 'connected' : 'disconnected'}`}>
          {connected ? 'CONNECTED' : 'DISCONNECTED'}
        </div>
      </header>

      <div className="app-body">
        <div className="left-panel">
          <ConnectionPanel connected={connected} setConnected={setConnected} />
          <FlightControls telemetry={telemetry} connected={connected} />
          <TelemetryDashboard telemetry={telemetry} />
        </div>

        <div className="main-panel">
          <div className="map-container">
            <MapView telemetry={telemetry} />
          </div>
          <div className="video-container">
            <VideoFeed />
          </div>
        </div>
      </div>
    </div>
  );
}

export default App;

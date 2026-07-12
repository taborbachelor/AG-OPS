import React, { useState, useEffect, useRef } from 'react';
import TopBar from './components/TopBar';
import NavRail from './components/NavRail';
import HudLeft from './components/HudLeft';
import HudRight from './components/HudRight';
import HudBottom from './components/HudBottom';
import MapView from './components/MapView';
import VideoFeed from './components/VideoFeed';
import ConnectionOverlay from './components/ConnectionOverlay';
import MissionPanel from './components/MissionPanel';
import SprayPanel from './components/SprayPanel';
import LaunchControl from './components/LaunchControl';
import SafetyPanel from './components/SafetyPanel';
import LogsPanel from './components/LogsPanel';
import RCPanel from './components/RCPanel';
import ControlsPanel from './components/ControlsPanel';
import FlightVitals from './components/FlightVitals';
import './App.css';

const DEFAULT_TELEMETRY = {
  connected: false, armed: false, mode: 'UNKNOWN',
  altitude: 0, airspeed: 0, groundspeed: 0, heading: 0,
  lat: 0, lon: 0, battery_voltage: 0, battery_current: 0,
  battery_level: null, pitch: 0, roll: 0, yaw: 0,
  gps_fix: 0, gps_satellites: 0,
  rc_channels: [], rc_rssi: 0, servo_outputs: [],
  mission_seq: 0, mission_count: 0, wp_dist: 0,
};

function App() {
  const [telemetry, setTelemetry] = useState(DEFAULT_TELEMETRY);
  const [connected, setConnected] = useState(false);
  const [backendUp, setBackendUp] = useState(true);
  const [showConnect, setShowConnect] = useState(false);

  // One active view, switched from the left nav rail.
  const [view, setView] = useState('fly'); // fly | plan | spray | safety | rc | controls | logs

  // Supervisor console: once the aircraft is genuinely in flight, the UI
  // switches to a minimal supervision layout and latches there until disarm.
  // The altitude/speed gate keeps bench tests (armed on the ground) in the
  // full-tool layout.
  const [flying, setFlying] = useState(false);
  const [toolsPeek, setToolsPeek] = useState(false);

  // Mission planning state
  const [waypoints, setWaypoints] = useState([]); // {id, command, lat, lon, alt}
  const [defaultAlt, setDefaultAlt] = useState(100);
  const nextId = useRef(1);

  // Spray workflow state (view 'spray'): customer field boundary, generated
  // coverage plan and the no-spray zones fetched alongside it.
  const [sprayField, setSprayField] = useState([]);     // [{lat, lon}]
  const [sprayDrawing, setSprayDrawing] = useState(false);
  const [sprayPlan, setSprayPlan] = useState(null);      // coverage API response
  const [sprayZones, setSprayZones] = useState(null);    // {water, trees, buildings}

  // Safety state
  const [fence, setFence] = useState({ enable: false, radius: 300, alt_max: 120, action: 1 });

  // Flight-log playback state. When playbackTelem is set, the map/HUD show the
  // recorded flight instead of live telemetry.
  const [playbackTelem, setPlaybackTelem] = useState(null);
  const [playbackPath, setPlaybackPath] = useState(null);
  const viewTelem = playbackTelem || telemetry;

  // Poll the backend for the true link state every 3s (single source of truth).
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

  // Telemetry WebSocket with auto-reconnect.
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
      ws.onmessage = (event) => {
        try { setTelemetry(JSON.parse(event.data)); } catch { /* ignore bad frame */ }
      };
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

  // Enter supervision mode when actually airborne; leave it on disarm.
  useEffect(() => {
    if (!connected || !telemetry.armed) {
      setFlying(false);
      setToolsPeek(false);
      return;
    }
    if (!flying && (telemetry.altitude > 8 || telemetry.groundspeed > 5)) {
      setFlying(true);
    }
  }, [telemetry, connected, flying]);

  // --- Mission editing handlers ---
  const addWaypoint = (latlng) => {
    setWaypoints((w) => [
      ...w,
      {
        id: nextId.current++,
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

  // Map clicks append spray-field vertices while the draw tool is armed.
  // Any boundary edit invalidates a previously generated plan so the shown
  // path can never disagree with the shown field.
  const addSprayVertex = (latlng) => {
    setSprayField((f) => [...f, { lat: latlng.lat, lon: latlng.lng }]);
    setSprayPlan(null);
  };

  // Full tools show when not flying, or when peeked mid-flight.
  const tools = !flying || toolsPeek;

  return (
    <div className="app">
      {/* Full-screen map background */}
      <div className="fullscreen-map">
        <MapView
          telemetry={viewTelem}
          planning={view === 'plan' && tools}
          waypoints={waypoints}
          onAddWaypoint={addWaypoint}
          onMoveWaypoint={moveWaypoint}
          fence={fence}
          playbackPath={playbackPath}
          sprayField={sprayField}
          sprayDrawing={view === 'spray' && tools && sprayDrawing}
          onAddSprayVertex={addSprayVertex}
          sprayPath={sprayPlan ? sprayPlan.waypoints : []}
          zones={view === 'spray' ? sprayZones : null}
        />
      </div>

      {/* Flight-critical status only */}
      <TopBar
        telemetry={viewTelem}
        connected={connected}
        backendUp={backendUp}
        playback={!!playbackTelem}
        onConnectClick={() => setShowConnect(!showConnect)}
      />

      {/* View switcher — slides away during flight */}
      <NavRail view={view} setView={setView} hidden={!tools} />
      {flying && (
        <button
          className="rail-peek"
          onClick={() => setToolsPeek((p) => !p)}
          title={toolsPeek ? 'Hide tools' : 'Show tools'}
        >
          {toolsPeek ? '‹' : '›'}
        </button>
      )}

      {/* Active view's panel (hidden in supervision mode unless peeked) */}
      {tools && view === 'fly' && !flying && <HudLeft telemetry={viewTelem} />}
      {tools && view === 'plan' && (
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
      )}
      {tools && view === 'spray' && (
        <SprayPanel
          connected={connected}
          field={sprayField}
          setField={setSprayField}
          drawing={sprayDrawing}
          setDrawing={setSprayDrawing}
          plan={sprayPlan}
          setPlan={setSprayPlan}
          zones={sprayZones}
          setZones={setSprayZones}
        />
      )}
      {tools && view === 'safety' && (
        <SafetyPanel connected={connected} fence={fence} setFence={setFence} />
      )}
      {tools && view === 'rc' && <RCPanel telemetry={telemetry} connected={connected} />}
      {tools && view === 'controls' && <ControlsPanel telemetry={telemetry} />}
      {tools && view === 'logs' && (
        <LogsPanel setPlaybackTelem={setPlaybackTelem} setPlaybackPath={setPlaybackPath} />
      )}

      {/* Instrument clusters: only in the full-tool layout */}
      {!flying && <HudRight telemetry={telemetry} connected={connected} />}
      {!flying && <HudBottom telemetry={viewTelem} />}

      {/* Setup phase: arm + takeoff flow. Flight phase: the vitals console. */}
      {!flying && view !== 'plan' && view !== 'logs' && (
        <LaunchControl telemetry={telemetry} connected={connected} />
      )}
      {flying && <FlightVitals telemetry={telemetry} />}

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

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
import AlertCenter from './components/AlertCenter';
import FlightSummary from './components/FlightSummary';
import ParamsPanel from './components/ParamsPanel';
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
  const [reconnecting, setReconnecting] = useState(false);
  const [showConnect, setShowConnect] = useState(false);

  // Post-flight debrief (set on disarm after a real flight).
  const [flightSummary, setFlightSummary] = useState(null);
  const flightRef = useRef(null);

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
  // Snap-to-field: one map click looks up the mapped parcel boundary under it.
  const [spraySnap, setSpraySnap] = useState(false);
  const [snapStatus, setSnapStatus] = useState('');

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
        setReconnecting(!!s.reconnecting);
      } catch {
        if (!alive) return;
        setBackendUp(false);
        setConnected(false);
        setReconnecting(false);
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

  // Flight accumulator: stats gathered while armed, debrief card on disarm.
  useEffect(() => {
    const t = telemetry;
    if (connected && t.armed) {
      if (!flightRef.current) {
        flightRef.current = {
          start: Date.now(), maxAlt: 0, maxSpd: 0, dist: 0,
          last: null, battStart: t.battery_level, battEnd: t.battery_level,
        };
      }
      const f = flightRef.current;
      f.maxAlt = Math.max(f.maxAlt, t.altitude || 0);
      f.maxSpd = Math.max(f.maxSpd, t.groundspeed || 0);
      if (t.lat && t.lon) {
        if (f.last) {
          const kx = 111320 * Math.cos((t.lat * Math.PI) / 180);
          f.dist += Math.hypot((t.lat - f.last.lat) * 111320, (t.lon - f.last.lon) * kx);
        }
        f.last = { lat: t.lat, lon: t.lon };
      }
      if (t.battery_level != null) f.battEnd = t.battery_level;
    } else if (flightRef.current) {
      const f = flightRef.current;
      flightRef.current = null;
      const dur = (Date.now() - f.start) / 1000;
      // Only debrief real flights — not bench arms or aborted starts.
      if (dur > 20 && (f.maxAlt > 5 || f.dist > 50)) {
        setFlightSummary({
          dur, maxAlt: f.maxAlt, maxSpd: f.maxSpd, dist: f.dist,
          battUsed: (f.battStart != null && f.battEnd != null)
            ? Math.max(0, f.battStart - f.battEnd) : null,
        });
      }
    }
  }, [telemetry, connected]);

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

  // Snap mode: the click doesn't add a vertex — it asks the backend for the
  // mapped field boundary containing that point. found:false is normal in
  // sparsely-mapped areas; the operator just draws instead.
  const snapClick = async (latlng) => {
    setSnapStatus('Looking up field boundary…');
    try {
      const r = await fetch(
        `http://localhost:8000/api/fields/snap?lat=${latlng.lat}&lon=${latlng.lng}&radius=2000`);
      const d = await r.json();
      if (r.ok && d.found) {
        setSprayField(d.polygon);
        setSprayPlan(null);
        setSpraySnap(false);
        setSnapStatus(`Snapped to mapped field boundary (${d.polygon.length} pts)`);
      } else {
        setSnapStatus('No mapped field boundary here — draw it manually');
      }
    } catch {
      setSnapStatus('Field lookup failed — draw manually');
    }
    setTimeout(() => setSnapStatus(''), 6000);
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
          sprayDrawing={view === 'spray' && tools && (sprayDrawing || spraySnap)}
          onAddSprayVertex={spraySnap ? snapClick : addSprayVertex}
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

      {/* Annunciator — quiet until something needs the operator */}
      <AlertCenter telemetry={telemetry} connected={connected} reconnecting={reconnecting} />

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
          snapping={spraySnap}
          setSnapping={setSpraySnap}
          snapStatus={snapStatus}
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
      {tools && view === 'params' && <ParamsPanel connected={connected} />}
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

      {/* Post-flight debrief */}
      {flightSummary && (
        <FlightSummary
          summary={flightSummary}
          onClose={() => setFlightSummary(null)}
          onReplay={() => { setFlightSummary(null); setView('logs'); }}
        />
      )}
    </div>
  );
}

export default App;

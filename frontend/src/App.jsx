import React, { useState, useEffect, useRef, useMemo } from 'react';
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
import { API, WS_BASE } from './api';
import './App.css';

// How long the vehicle must report disarmed (while connected) before the UI
// treats the flight as over. Covers the backend's post-reconnect telemetry
// reset, which reports armed=false until the next 1Hz HEARTBEAT is parsed.
const DISARM_CONFIRM_MS = 2000;

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
  // Timestamp of the first consecutive disarmed frame (0 = armed/unknown).
  const disarmedAt = useRef(0);

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
  const [sprayField, setSprayField] = useState([]);     // working draft [{lat,lon}]
  const [sprayDrawing, setSprayDrawing] = useState(false);
  const [sprayFields, setSprayFields] = useState([]);    // committed job fields [{polygon, acres, source}]
  const [sprayArea, setSprayArea] = useState([]);        // selection area for auto-detect
  const [areaDrawing, setAreaDrawing] = useState(false);
  const [sprayPlan, setSprayPlan] = useState(null);      // plan_multi response
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
        const r = await fetch(`${API}/connection/status`);
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
    if (!connected) return;
    let closedByUs = false;
    let ws;
    let retry;
    const open = () => {
      ws = new WebSocket(`${WS_BASE}/telemetry/ws`);
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

  // Wipe stale telemetry only when the link is definitively down. During the
  // backend's auto-reconnect window (a transient radio dropout mid-flight) we
  // hold the last-known frame: resetting to DEFAULT would fake a disarm,
  // dropping the flight console and popping a false debrief while airborne.
  useEffect(() => {
    if (!connected && !reconnecting) setTelemetry(DEFAULT_TELEMETRY);
  }, [connected, reconnecting]);

  // Debounced disarm detection. Right after the backend auto-reconnects it
  // resets its TelemetryData, so the first WS frames can report armed=false
  // for up to ~1s (until the next HEARTBEAT parses) even though the aircraft
  // is still flying. Only treat the vehicle as disarmed once it has reported
  // disarmed continuously for DISARM_CONFIRM_MS while connected.
  // (Declared before the effects below so they see this frame's update.)
  useEffect(() => {
    if (!connected || telemetry.armed) {
      disarmedAt.current = 0;
    } else if (!disarmedAt.current) {
      disarmedAt.current = Date.now();
    }
  }, [telemetry, connected]);

  // Enter supervision mode when actually airborne; leave it on disarm.
  // While the backend is auto-reconnecting after a link drop the aircraft is
  // still flying — hold the supervision console instead of swapping back to
  // the full-tool launch layout.
  useEffect(() => {
    if (reconnecting) return;
    if (!connected) {
      setFlying(false);
      setToolsPeek(false);
      return;
    }
    if (!telemetry.armed) {
      // Confirmed disarm only — ignore the transient post-reconnect frames.
      if (disarmedAt.current && Date.now() - disarmedAt.current > DISARM_CONFIRM_MS) {
        setFlying(false);
        setToolsPeek(false);
      }
      return;
    }
    if (!flying && (telemetry.altitude > 8 || telemetry.groundspeed > 5)) {
      setFlying(true);
    }
  }, [telemetry, connected, reconnecting, flying]);

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
    } else if (flightRef.current && connected && !t.armed
        && disarmedAt.current && Date.now() - disarmedAt.current > DISARM_CONFIRM_MS) {
      // Close out the flight only on a CONFIRMED, observed disarm. A dropped
      // link (reconnecting or lost) merely pauses the accumulator: flushing
      // there would show a bogus "Flight Complete" mid-air and split the
      // stats across the outage. If the link returns with the aircraft still
      // armed, the same accumulator (and battery baseline) simply continues.
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
    if (areaDrawing) {
      setSprayArea((a) => [...a, { lat: latlng.lat, lon: latlng.lng }]);
    } else {
      setSprayField((f) => [...f, { lat: latlng.lat, lon: latlng.lng }]);
    }
    setSprayPlan(null);
  };

  // Snap mode: the click doesn't add a vertex — it asks the backend for the
  // mapped field boundary containing that point, then commits it straight
  // into the job. found:false is normal in sparsely-mapped areas.
  const snapClick = async (latlng) => {
    setSnapStatus('Looking up field boundary…');
    try {
      const r = await fetch(
        `${API}/fields/snap?lat=${latlng.lat}&lon=${latlng.lng}&radius=2000`);
      const d = await r.json();
      if (r.ok && d.found) {
        setSprayFields((f) => [...f, { polygon: d.polygon, acres: null, source: 'snap' }]);
        setSprayPlan(null);
        setSpraySnap(false);
        setSnapStatus(`Snapped field added to job (${d.polygon.length} pts)`);
      } else {
        setSnapStatus('No mapped field boundary here — draw it manually');
      }
    } catch {
      setSnapStatus('Field lookup failed — draw manually');
    }
    setTimeout(() => setSnapStatus(''), 6000);
  };

  // Whole-job flight legs for the map, derived from the plan_multi response:
  // spray passes solid, in-field hops faint, inter-field transits orange,
  // home legs purple. Waypoints are (start,end) pairs per spray segment.
  const sprayLegs = useMemo(() => {
    const p = sprayPlan;
    if (!p || !p.flight_order) return [];
    const byIndex = Object.fromEntries(p.fields.map((f) => [f.index, f]));
    const legs = [];
    for (const stop of p.flight_order) {
      let wps = byIndex[stop.index].waypoints;
      if (stop.reversed) wps = [...wps].reverse();
      for (let i = 0; i + 1 < wps.length; i += 2) {
        legs.push({ kind: 'spray', pts: [[wps[i].lat, wps[i].lon], [wps[i + 1].lat, wps[i + 1].lon]] });
        if (i + 2 < wps.length) {
          legs.push({ kind: 'hop', pts: [[wps[i + 1].lat, wps[i + 1].lon], [wps[i + 2].lat, wps[i + 2].lon]] });
        }
      }
    }
    const ts = p.transits || [];
    // With a home position the planner emits n+1 transit legs (out + back);
    // without, n-1 (between fields only).
    const hasHome = ts.length === p.flight_order.length + 1;
    ts.forEach((t, ti) => {
      const kind = hasHome && (ti === 0 || ti === ts.length - 1) ? 'home' : 'transit';
      legs.push({ kind, pts: t.pts.map((q) => [q.lat, q.lon]) });
    });
    return legs;
  }, [sprayPlan]);

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
          sprayFields={sprayFields.map((f) => ({ polygon: f.polygon, holes: f.holes || [] }))}
          sprayArea={sprayArea}
          sprayDrawing={view === 'spray' && tools && (sprayDrawing || spraySnap || areaDrawing)}
          onAddSprayVertex={spraySnap ? snapClick : addSprayVertex}
          sprayLegs={view === 'spray' ? sprayLegs : []}
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
          draft={sprayField}
          setDraft={setSprayField}
          fields={sprayFields}
          setFields={setSprayFields}
          area={sprayArea}
          setArea={setSprayArea}
          drawing={sprayDrawing}
          setDrawing={setSprayDrawing}
          areaDrawing={areaDrawing}
          setAreaDrawing={setAreaDrawing}
          snapping={spraySnap}
          setSnapping={setSpraySnap}
          snapStatus={snapStatus}
          plan={sprayPlan}
          setPlan={setSprayPlan}
          zones={sprayZones}
          setZones={setSprayZones}
          homePos={telemetry.home_lat ? { lat: telemetry.home_lat, lon: telemetry.home_lon } : null}
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

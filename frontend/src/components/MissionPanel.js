import React, { useState } from 'react';

const API = 'http://localhost:8000/api';
const COMMANDS = ['TAKEOFF', 'WAYPOINT', 'LOITER', 'LAND', 'RTL'];

const CMD_COLOR = {
  TAKEOFF: '#00e676',
  WAYPOINT: '#00e5ff',
  LOITER: '#ff9100',
  LAND: '#ff1744',
  RTL: '#b388ff',
};

function MissionPanel({
  connected, waypoints, setWaypoints, defaultAlt, setDefaultAlt,
  updateWaypoint, removeWaypoint, clearMission, nextId,
}) {
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);

  const flash = (msg) => {
    setStatus(msg);
    setTimeout(() => setStatus(''), 4000);
  };

  const upload = async () => {
    if (waypoints.length === 0) { flash('Add at least one waypoint'); return; }
    setBusy(true);
    try {
      const items = waypoints.map((w) => ({
        command: w.command, lat: w.lat, lon: w.lon, alt: Number(w.alt), param1: 0,
      }));
      const res = await fetch(`${API}/mission/upload`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items }),
      });
      const data = await res.json();
      flash(res.ok ? `Uploaded ${data.count} waypoints ✓` : `Upload failed: ${data.detail || res.status}`);
    } catch (e) {
      flash('Upload error — is the backend running?');
    }
    setBusy(false);
  };

  const download = async () => {
    setBusy(true);
    try {
      const res = await fetch(`${API}/mission/download`);
      const data = await res.json();
      if (res.ok) {
        setWaypoints((data.items || []).map((it) => ({
          id: nextId.current++,
          command: it.command, lat: it.lat, lon: it.lon, alt: Math.round(it.alt),
        })));
        flash(`Downloaded ${data.items.length} waypoints ✓`);
      } else {
        flash(`Download failed: ${data.detail || res.status}`);
      }
    } catch (e) {
      flash('Download error');
    }
    setBusy(false);
  };

  const start = async () => {
    setBusy(true);
    try {
      const res = await fetch(`${API}/mission/start`, { method: 'POST' });
      flash(res.ok ? 'Mission started (AUTO) ✓' : 'Start failed');
    } catch (e) {
      flash('Start error');
    }
    setBusy(false);
  };

  return (
    <div className="mission-panel glass-panel">
      <div className="panel-title" style={{ marginBottom: 12 }}>Mission Plan</div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Default alt</span>
        <input
          type="number"
          value={defaultAlt}
          onChange={(e) => setDefaultAlt(Number(e.target.value))}
          style={{ width: 64, padding: '4px 8px' }}
        />
        <span style={{ fontSize: 11, color: 'var(--text-dim)' }}>m</span>
        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-dim)' }}>
          {waypoints.length} pts
        </span>
      </div>

      {waypoints.length === 0 ? (
        <div style={{
          padding: '20px 8px', textAlign: 'center', fontSize: 12,
          color: 'var(--text-dim)', lineHeight: 1.6,
        }}>
          Click the map to drop waypoints.<br />Drag markers to reposition.
        </div>
      ) : (
        <div className="wp-list">
          {waypoints.map((w, idx) => (
            <div key={w.id} className="wp-row">
              <span className="wp-seq" style={{ background: CMD_COLOR[w.command] }}>{idx + 1}</span>
              <select
                value={w.command}
                onChange={(e) => updateWaypoint(w.id, { command: e.target.value })}
                style={{ flex: 1, padding: '3px 6px', fontSize: 11 }}
              >
                {COMMANDS.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
              {w.command !== 'RTL' && (
                <input
                  type="number"
                  value={w.alt}
                  onChange={(e) => updateWaypoint(w.id, { alt: Number(e.target.value) })}
                  style={{ width: 52, padding: '3px 6px', fontSize: 11 }}
                  title="Altitude (m)"
                />
              )}
              <button className="wp-del" onClick={() => removeWaypoint(w.id)} title="Delete">×</button>
            </div>
          ))}
        </div>
      )}

      <div style={{ display: 'flex', gap: 6, marginTop: 12 }}>
        <button className="control-btn success" onClick={upload} disabled={!connected || busy} style={{ flex: 1 }}>
          Upload
        </button>
        <button className="control-btn" onClick={download} disabled={!connected || busy} style={{ flex: 1 }}>
          Download
        </button>
      </div>
      <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
        <button className="control-btn" onClick={start} disabled={!connected || busy} style={{ flex: 1 }}>
          Start (AUTO)
        </button>
        <button className="control-btn danger" onClick={clearMission} disabled={busy} style={{ flex: 1 }}>
          Clear
        </button>
      </div>

      {status && (
        <div style={{
          marginTop: 10, padding: '6px 10px', fontSize: 11,
          background: 'rgba(0,229,255,0.08)', border: '1px solid var(--glass-border)',
          borderRadius: 6, color: 'var(--text-primary)',
        }}>
          {status}
        </div>
      )}

      {!connected && (
        <div style={{ marginTop: 8, fontSize: 10, color: 'var(--accent-orange)', textAlign: 'center' }}>
          Connect to a vehicle to upload/start
        </div>
      )}
    </div>
  );
}

export default MissionPanel;

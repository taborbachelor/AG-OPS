import React, { useState, useEffect, useRef } from 'react';

const API = 'http://localhost:8000/api';

// Typical AETR-ish channel roles on a plane (RadioMaster default). CH5+ vary.
const ROLES = {
  1: 'Roll / Ail', 2: 'Pitch / Elev', 3: 'Throttle', 4: 'Yaw / Rud',
  5: 'AUX / Mode', 6: 'AUX', 7: 'AUX', 8: 'AUX',
};

function ChannelBar({ n, value }) {
  const pct = Math.max(0, Math.min(100, (value - 1000) / 10));
  const live = value >= 900 && value <= 2100;
  return (
    <div className="rc-row">
      <div className="rc-ch">CH{n}</div>
      <div className="rc-role">{ROLES[n] || 'AUX'}</div>
      <div className="rc-track">
        <div className="rc-center" />
        <div className="rc-fill" style={{ width: `${pct}%`,
          background: live ? 'var(--accent)' : 'var(--text-dim)' }} />
      </div>
      <div className="rc-val">{value || '--'}</div>
    </div>
  );
}

function RCPanel({ telemetry, connected }) {
  const chans = telemetry.rc_channels || [];
  const rssi = telemetry.rc_rssi || 0;
  const hasInput = chans.some((v) => v >= 900 && v <= 2100);

  const [manual, setManual] = useState(false);
  const [gpName, setGpName] = useState(null);
  const wsRef = useRef(null);

  // Track gamepad connect/disconnect for the status line.
  useEffect(() => {
    const on = (e) => setGpName(e.gamepad.id);
    const off = () => setGpName(null);
    window.addEventListener('gamepadconnected', on);
    window.addEventListener('gamepaddisconnected', off);
    const pads = navigator.getGamepads ? navigator.getGamepads() : [];
    const gp = Array.from(pads).find((p) => p);
    if (gp) setGpName(gp.id);
    return () => {
      window.removeEventListener('gamepadconnected', on);
      window.removeEventListener('gamepaddisconnected', off);
    };
  }, []);

  // While manual control is on, stream gamepad axes -> RC override WebSocket at ~25Hz.
  useEffect(() => {
    if (!manual) return;
    const ws = new WebSocket('ws://localhost:8000/api/vehicle/rc');
    wsRef.current = ws;
    let raf, last = 0;
    const loop = (ts) => {
      const pads = navigator.getGamepads ? navigator.getGamepads() : [];
      const gp = Array.from(pads).find((p) => p);
      if (gp && ws.readyState === 1 && ts - last > 40) {
        last = ts;
        const out = [];
        for (let i = 0; i < 8; i++) {
          const a = gp.axes[i];
          out.push(a === undefined ? 0 : Math.round(1500 + a * 500));
        }
        ws.send(JSON.stringify({ channels: out }));
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => { cancelAnimationFrame(raf); ws.close(); };
  }, [manual]);

  const post = (path, body) => fetch(`${API}${path}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  }).catch(() => {});

  return (
    <div className="rc-panel glass-panel">
      <div className="panel-title" style={{ marginBottom: 10 }}>RC Input · RadioMaster</div>

      <div className="rc-status-line">
        <span className={`dot ${hasInput ? 'green' : 'red'}`} />
        <span>{hasInput ? 'RECEIVING' : 'NO RC SIGNAL'}</span>
        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-dim)' }}>
          RSSI {rssi ? `${Math.round((rssi / 254) * 100)}%` : '--'}
        </span>
      </div>

      {chans.length === 0 ? (
        <div className="rc-empty">
          No RC channels reported.<br />
          For a real receiver: bind it to the RadioMaster and wire it to the Cube's
          RC input. Or use <b>Manual Control</b> below to fly the sim from the
          RadioMaster in USB-Joystick mode.
        </div>
      ) : (
        <div className="rc-list">
          {chans.map((v, i) => <ChannelBar key={i} n={i + 1} value={v} />)}
        </div>
      )}

      {/* Manual control: fly from a laptop-connected transmitter/gamepad */}
      <div className="rc-manual">
        <div className="rc-manual-head">
          <span>MANUAL CONTROL</span>
          <label className={`mini-toggle ${manual ? 'on' : ''}`}>
            <input type="checkbox" checked={manual}
              onChange={(e) => setManual(e.target.checked)} disabled={!connected} />
            {manual ? 'LIVE' : 'OFF'}
          </label>
        </div>
        <div className="rc-gp-status">
          <span className={`dot ${gpName ? 'green' : 'red'}`} />
          <span>{gpName ? gpName.slice(0, 30) : 'No gamepad — plug in RadioMaster (USB Joystick)'}</span>
        </div>
        <div className="rc-manual-btns">
          <button className="control-btn" onClick={() => post('/vehicle/mode', { mode: 'FBWA' })}
            disabled={!connected}>FBWA</button>
          <button className="control-btn" onClick={() => post('/vehicle/mode', { mode: 'MANUAL' })}
            disabled={!connected}>MANUAL</button>
          <button className="control-btn success" onClick={() => post('/vehicle/arm', { force: true })}
            disabled={!connected}>ARM</button>
          <button className="control-btn danger" onClick={() => post('/vehicle/disarm', { force: true })}
            disabled={!connected}>DISARM</button>
        </div>
        {manual && (
          <div className="rc-hint" style={{ color: 'var(--accent-orange)' }}>
            Streaming sticks → sim. Set <b>FBWA</b> (stabilized) + <b>ARM</b>, then fly.
          </div>
        )}
      </div>

      <div className="rc-hint">
        Move the sticks/switches — the bars track them live.
      </div>
    </div>
  );
}

export default RCPanel;

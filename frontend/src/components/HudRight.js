import React, { useRef, useState } from 'react';

const API = 'http://localhost:8000/api';

function Compass({ heading }) {
  return (
    <div className="compass">
      <span className="compass-label n">N</span>
      <span className="compass-label s">S</span>
      <span className="compass-label e">E</span>
      <span className="compass-label w">W</span>
      <div
        className="compass-arrow"
        style={{ transform: `translate(-50%, -100%) rotate(${heading}deg)` }}
      />
      <span className="compass-heading">{heading}°</span>
    </div>
  );
}

function BatteryBar({ voltage, current, level }) {
  const pct = level ?? 100;
  const color = pct < 20 ? 'var(--accent-red)' :
    pct < 50 ? 'var(--accent-orange)' : 'var(--accent-green)';

  return (
    <div className="battery-bar glass-panel">
      <div className="battery-bar-track">
        <div
          className="battery-bar-fill"
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
      <span className="battery-bar-label" style={{ color }}>
        {voltage.toFixed(1)}V
      </span>
      <span style={{ fontSize: 9, color: 'var(--text-dim)' }}>
        {current.toFixed(1)}A
      </span>
    </div>
  );
}

function HudRight({ telemetry, connected }) {
  const t = telemetry;
  const [note, setNote] = useState('');
  const noteTimer = useRef(null);

  const fail = (m) => {
    setNote(m);
    clearTimeout(noteTimer.current);
    noteTimer.current = setTimeout(() => setNote(''), 8000);
  };

  // A rejected mode change (4xx/5xx or no backend) must be visible — the
  // status poll only tracks link state, not command acks.
  const setMode = async (mode) => {
    try {
      const res = await fetch(`${API}/vehicle/mode`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      });
      if (!res.ok) {
        let detail = '';
        try { detail = (await res.json()).detail; } catch { /* no body */ }
        fail(`${mode} FAILED — ${detail || `HTTP ${res.status}`}`);
      }
    } catch (e) {
      fail(`${mode} FAILED — no response from backend`);
    }
  };

  return (
    <div className="hud-right">
      <Compass heading={t.heading} />

      <BatteryBar
        voltage={t.battery_voltage}
        current={t.battery_current}
        level={t.battery_level}
      />

      <div className="side-controls">
        <button
          className="control-btn danger"
          onClick={() => setMode('RTL')}
          disabled={!connected}
          style={{ fontWeight: 700 }}
        >
          RTL
        </button>
        <button
          className="control-btn"
          onClick={() => setMode('AUTO')}
          disabled={!connected}
        >
          AUTO
        </button>
        <button
          className="control-btn"
          onClick={() => setMode('STABILIZE')}
          disabled={!connected}
        >
          STAB
        </button>
        <button
          className="control-btn"
          onClick={() => setMode('MANUAL')}
          disabled={!connected}
        >
          MANUAL
        </button>
        {note && <div className="vitals-note">{note}</div>}
      </div>
    </div>
  );
}

export default HudRight;

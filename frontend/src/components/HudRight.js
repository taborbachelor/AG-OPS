import React from 'react';

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

  const setMode = async (mode) => {
    try {
      await fetch(`${API}/vehicle/mode`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      });
    } catch (e) { console.error(e); }
  };

  const toggleArm = async () => {
    try {
      const endpoint = t.armed ? 'disarm' : 'arm';
      await fetch(`${API}/vehicle/${endpoint}`, { method: 'POST' });
    } catch (e) { console.error(e); }
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
          className={`control-btn ${t.armed ? 'danger' : 'success'}`}
          onClick={toggleArm}
          disabled={!connected}
        >
          {t.armed ? 'DISARM' : 'ARM'}
        </button>
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
      </div>
    </div>
  );
}

export default HudRight;

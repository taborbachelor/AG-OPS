import React, { useState } from 'react';

import { API } from '../api';

const MODES = ['MANUAL', 'STABILIZE', 'FBWA', 'FBWB', 'AUTO', 'RTL', 'LOITER', 'GUIDED', 'CIRCLE', 'LAND'];

function FlightControls({ telemetry, connected }) {
  const [selectedMode, setSelectedMode] = useState('STABILIZE');

  const setMode = async (mode) => {
    try {
      await fetch(`${API}/vehicle/mode`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      });
    } catch (e) {
      console.error('Failed to set mode:', e);
    }
  };

  const arm = async () => {
    try {
      await fetch(`${API}/vehicle/arm`, { method: 'POST' });
    } catch (e) {
      console.error('Failed to arm:', e);
    }
  };

  const disarm = async () => {
    try {
      await fetch(`${API}/vehicle/disarm`, { method: 'POST' });
    } catch (e) {
      console.error('Failed to disarm:', e);
    }
  };

  return (
    <div className="panel">
      <div className="panel-title">Flight Controls</div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <span style={{ fontSize: 12, color: '#94a3b8' }}>Mode:</span>
        <span style={{
          fontSize: 14, fontWeight: 700,
          color: telemetry.mode === 'RTL' ? '#f59e0b' : '#60a5fa',
          fontFamily: 'monospace',
        }}>
          {telemetry.mode}
        </span>
        <span style={{
          fontSize: 12, fontWeight: 600, marginLeft: 'auto',
          color: telemetry.armed ? '#34d399' : '#94a3b8',
        }}>
          {telemetry.armed ? 'ARMED' : 'DISARMED'}
        </span>
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
        <select
          value={selectedMode}
          onChange={(e) => setSelectedMode(e.target.value)}
          disabled={!connected}
          style={{ flex: 1 }}
        >
          {MODES.map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>
        <button className="btn btn-primary" onClick={() => setMode(selectedMode)} disabled={!connected}>
          Set
        </button>
      </div>

      <div style={{ display: 'flex', gap: 8 }}>
        {!telemetry.armed ? (
          <button className="btn btn-success" onClick={arm} disabled={!connected} style={{ flex: 1 }}>
            ARM
          </button>
        ) : (
          <button className="btn btn-danger" onClick={disarm} disabled={!connected} style={{ flex: 1 }}>
            DISARM
          </button>
        )}
        <button
          className="btn btn-danger"
          onClick={() => setMode('RTL')}
          disabled={!connected}
          style={{ flex: 1, fontWeight: 700 }}
        >
          RTL
        </button>
      </div>
    </div>
  );
}

export default FlightControls;

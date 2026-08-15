import React, { useState, useEffect } from 'react';

import { API } from '../api';

function ConnectionOverlay({ connected, setConnected, onClose }) {
  const [ports, setPorts] = useState([]);
  const [selectedPort, setSelectedPort] = useState('');
  const [baud, setBaud] = useState(57600);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    fetchPorts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const fetchPorts = async () => {
    try {
      const res = await fetch(`${API}/connection/ports`);
      const data = await res.json();
      setPorts(data);
      if (data.length > 0 && !selectedPort) setSelectedPort(data[0].device);
    } catch (e) {
      console.error('Failed to fetch ports:', e);
    }
  };

  // Core connect used by both Quick Connect buttons and the manual form.
  const doConnect = async (cs, baudRate) => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`${API}/connection/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ connection_string: cs, baud: baudRate }),
      });
      // 400 = "already connected" -> the backend is linked, treat as success.
      if (res.ok || res.status === 400) {
        setConnected(true);
        onClose();
      } else {
        const d = await res.json().catch(() => ({}));
        setError(d.detail || `Connect failed (${res.status})`);
      }
    } catch (e) {
      setError('Could not reach the backend');
    }
    setLoading(false);
  };

  // Simulator quick-connect: ask the backend to spawn the bundled SITL first
  // (no-op if it's already running or the binary isn't shipped), then link.
  const connectSimulator = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`${API}/sim/start`, { method: 'POST' });
      if (!res.ok && res.status !== 404) {
        const d = await res.json().catch(() => ({}));
        setError(d.detail || 'Could not start the simulator');
        setLoading(false);
        return;
      }
    } catch (e) {
      /* backend unreachable — doConnect will surface that */
    }
    await doConnect('tcp:127.0.0.1:5760', 57600);
  };

  const handleDisconnect = async () => {
    try {
      await fetch(`${API}/connection/disconnect`, { method: 'POST' });
      setConnected(false);
    } catch (e) {
      console.error('Disconnect failed:', e);
    }
  };

  return (
    <div className="connection-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="glass-panel">
        <div className="connection-title">Connection</div>

        {!connected && (
          <div className="qc-section">
            <div className="qc-title">QUICK CONNECT</div>
            {ports.map((p) => (
              <button key={p.device} className="qc-btn" disabled={loading}
                onClick={() => doConnect(p.device, 115200)}>
                <span>⚡ {p.device} <span style={{ color: 'var(--text-dim)' }}>— {p.description}</span></span>
                <span className="qc-sub">CUBE USB · 115200</span>
              </button>
            ))}
            {ports.length === 0 && (
              <div className="qc-none">
                No COM ports detected — plug in the Cube / telemetry radio
                <button className="control-btn" onClick={fetchPorts}
                  style={{ marginLeft: 8, fontSize: 11, padding: '3px 10px' }}>↻ Rescan</button>
              </div>
            )}
            <button className="qc-btn" disabled={loading}
              onClick={connectSimulator}>
              <span>🖥 Simulator (SITL)</span>
              <span className="qc-sub">AUTO-STARTS · TCP 5760</span>
            </button>
          </div>
        )}

        <div className="qc-title">MANUAL</div>
        <div className="connection-row">
          <select
            value={selectedPort}
            onChange={(e) => setSelectedPort(e.target.value)}
            disabled={connected}
            style={{ flex: 1 }}
          >
            {ports.length === 0 && <option value="">No ports detected</option>}
            {ports.map((p) => (
              <option key={p.device} value={p.device}>
                {p.device} — {p.description}
              </option>
            ))}
          </select>
          <button className="control-btn" onClick={fetchPorts} disabled={connected}>
            ↻
          </button>
        </div>

        <div className="connection-row">
          <select
            value={baud}
            onChange={(e) => setBaud(Number(e.target.value))}
            disabled={connected}
            style={{ width: '40%' }}
          >
            <option value={9600}>9600</option>
            <option value={57600}>57600</option>
            <option value={115200}>115200</option>
          </select>
          <span style={{ fontSize: 11, color: 'var(--text-dim)', alignSelf: 'center' }}>BAUD</span>
        </div>

        <input
          type="text"
          placeholder="Manual: COM3, tcp:127.0.0.1:5760, udp:14550"
          value={selectedPort}
          onChange={(e) => setSelectedPort(e.target.value)}
          disabled={connected}
        />

        {error && <div className="qc-error">{error}</div>}

        <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
          {!connected ? (
            <button
              className="control-btn success"
              onClick={() => doConnect(selectedPort, baud)}
              disabled={loading || !selectedPort}
              style={{ flex: 1 }}
            >
              {loading ? 'LINKING...' : 'CONNECT'}
            </button>
          ) : (
            <button
              className="control-btn danger"
              onClick={handleDisconnect}
              style={{ flex: 1 }}
            >
              DISCONNECT
            </button>
          )}
          <button className="control-btn" onClick={onClose}>
            CLOSE
          </button>
        </div>
      </div>
    </div>
  );
}

export default ConnectionOverlay;

import React, { useState, useEffect } from 'react';

const API = 'http://localhost:8000/api';

function ConnectionOverlay({ connected, setConnected, onClose }) {
  const [ports, setPorts] = useState([]);
  const [selectedPort, setSelectedPort] = useState('');
  const [baud, setBaud] = useState(57600);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetchPorts();
  }, []);

  const fetchPorts = async () => {
    try {
      const res = await fetch(`${API}/connection/ports`);
      const data = await res.json();
      setPorts(data);
      if (data.length > 0) setSelectedPort(data[0].device);
    } catch (e) {
      console.error('Failed to fetch ports:', e);
    }
  };

  const handleConnect = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API}/connection/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ connection_string: selectedPort, baud }),
      });
      // 400 = "already connected" -> the backend is linked (e.g. to SITL), so
      // treat it as success and let the dashboard start streaming.
      if (res.ok || res.status === 400) {
        setConnected(true);
        onClose();
      }
    } catch (e) {
      console.error('Connection failed:', e);
    }
    setLoading(false);
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

        <div className="connection-row">
          <select
            value={selectedPort}
            onChange={(e) => setSelectedPort(e.target.value)}
            disabled={connected}
            style={{ flex: 1 }}
          >
            {ports.length === 0 && <option>No ports detected</option>}
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
          placeholder="Manual: tcp:127.0.0.1:5760 or udp:14550"
          value={selectedPort}
          onChange={(e) => setSelectedPort(e.target.value)}
          disabled={connected}
        />

        <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
          {!connected ? (
            <button
              className="control-btn success"
              onClick={handleConnect}
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

import React, { useState, useEffect } from 'react';

const API = 'http://localhost:8000/api';

function ConnectionPanel({ connected, setConnected }) {
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
      if (res.ok) setConnected(true);
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
    <div className="panel">
      <div className="panel-title">Connection</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ display: 'flex', gap: 8 }}>
          <select
            value={selectedPort}
            onChange={(e) => setSelectedPort(e.target.value)}
            style={{ flex: 1 }}
            disabled={connected}
          >
            {ports.length === 0 && <option>No ports found</option>}
            {ports.map((p) => (
              <option key={p.device} value={p.device}>
                {p.device} - {p.description}
              </option>
            ))}
          </select>
          <button className="btn btn-primary" onClick={fetchPorts} disabled={connected} style={{ padding: '6px 8px' }}>
            ↻
          </button>
        </div>

        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <label style={{ fontSize: 12, color: '#94a3b8' }}>Baud:</label>
          <select value={baud} onChange={(e) => setBaud(Number(e.target.value))} disabled={connected}>
            <option value={9600}>9600</option>
            <option value={57600}>57600</option>
            <option value={115200}>115200</option>
          </select>
        </div>

        {!connected ? (
          <button className="btn btn-success" onClick={handleConnect} disabled={loading || !selectedPort}>
            {loading ? 'Connecting...' : 'Connect'}
          </button>
        ) : (
          <button className="btn btn-danger" onClick={handleDisconnect}>
            Disconnect
          </button>
        )}

        <input
          type="text"
          placeholder="Or enter manually: tcp:127.0.0.1:5760"
          value={selectedPort}
          onChange={(e) => setSelectedPort(e.target.value)}
          disabled={connected}
          style={{ fontSize: 11 }}
        />
      </div>
    </div>
  );
}

export default ConnectionPanel;

import React from 'react';

// Typical AETR-ish channel roles on a plane (RadioMaster default). CH5+ vary.
const ROLES = {
  1: 'Roll / Ail', 2: 'Pitch / Elev', 3: 'Throttle', 4: 'Yaw / Rud',
  5: 'AUX / Mode', 6: 'AUX', 7: 'AUX', 8: 'AUX',
};

function ChannelBar({ n, value }) {
  // Map 1000–2000 µs to 0–100%. Values outside that clamp.
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

function RCPanel({ telemetry }) {
  const chans = telemetry.rc_channels || [];
  const rssi = telemetry.rc_rssi || 0;
  const hasInput = chans.some((v) => v >= 900 && v <= 2100);

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
          Check that a receiver is bound to the RadioMaster and wired to the Cube's
          RC input, and that the transmitter is on.
        </div>
      ) : (
        <div className="rc-list">
          {chans.map((v, i) => <ChannelBar key={i} n={i + 1} value={v} />)}
        </div>
      )}

      <div className="rc-hint">
        Move the sticks and flip the switches — the bars should track them live.
      </div>
    </div>
  );
}

export default RCPanel;

import React from 'react';

const fmtDur = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

// Post-flight debrief card, shown automatically after disarm.
function FlightSummary({ summary, onClose, onReplay }) {
  return (
    <div className="summary-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="glass-panel summary-card">
        <div className="connection-title">Flight Complete</div>
        <div className="summary-grid">
          <div className="vd-item"><span className="vd-label">TIME</span>
            <span className="vd-value">{fmtDur(summary.dur)}</span></div>
          <div className="vd-item"><span className="vd-label">DISTANCE</span>
            <span className="vd-value">{(summary.dist / 1000).toFixed(2)} km</span></div>
          <div className="vd-item"><span className="vd-label">MAX ALT</span>
            <span className="vd-value">{summary.maxAlt.toFixed(0)} m</span></div>
          <div className="vd-item"><span className="vd-label">MAX SPD</span>
            <span className="vd-value">{summary.maxSpd.toFixed(1)} m/s</span></div>
          <div className="vd-item"><span className="vd-label">BATTERY USED</span>
            <span className="vd-value">{summary.battUsed != null ? `${summary.battUsed}%` : '—'}</span></div>
        </div>
        <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
          <button className="control-btn" onClick={onReplay} style={{ flex: 1 }}>
            View replay
          </button>
          <button className="control-btn success" onClick={onClose} style={{ flex: 1 }}>
            Done
          </button>
        </div>
      </div>
    </div>
  );
}

export default FlightSummary;

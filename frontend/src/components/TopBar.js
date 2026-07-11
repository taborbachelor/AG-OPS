import React from 'react';

function TopBar({ telemetry, connected, planning, onPlanClick, onConnectClick }) {
  const t = telemetry;
  const gpsLabel = ['--', 'NO FIX', '2D', '3D', 'DGPS', 'RTK'][t.gps_fix] || `${t.gps_fix}`;
  const batteryColor = (t.battery_level ?? 100) < 20 ? 'var(--accent-red)' :
    (t.battery_level ?? 100) < 50 ? 'var(--accent-orange)' : 'var(--accent-green)';

  return (
    <div className="top-bar">
      <div className="top-bar-left">
        <span className="brand-title">GCS</span>
        <div className="status-chip" onClick={onConnectClick} style={{ cursor: 'pointer' }}>
          <span className={`dot ${connected ? 'green' : 'red'}`}></span>
          <span>{connected ? 'LINKED' : 'OFFLINE'}</span>
        </div>
        <div
          className={`status-chip ${planning ? 'plan-active' : ''}`}
          onClick={onPlanClick}
          style={{ cursor: 'pointer' }}
        >
          <span className="dot cyan"></span>
          <span>{planning ? 'PLANNING' : 'PLAN'}</span>
        </div>
      </div>

      <div className="top-bar-center">
        <div className={`mode-badge ${t.armed ? 'armed' : ''}`}>
          {t.armed ? '● ' : ''}{t.mode}
        </div>

        <div className="status-chip">
          <span>GPS</span>
          <span className="status-value">{gpsLabel}</span>
          <span style={{ color: 'var(--text-dim)' }}>|</span>
          <span className="status-value">{t.gps_satellites}</span>
          <span style={{ fontSize: 9, color: 'var(--text-dim)' }}>SAT</span>
        </div>

        <div className="status-chip">
          <span style={{ fontSize: 10 }}>⚡</span>
          <span className="status-value" style={{ color: batteryColor }}>
            {t.battery_voltage.toFixed(1)}V
          </span>
          {t.battery_level !== null && (
            <span className="status-value" style={{ color: batteryColor }}>
              {t.battery_level}%
            </span>
          )}
        </div>
      </div>

      <div className="top-bar-right">
        <div className="status-chip">
          <span className="status-value">{t.lat.toFixed(5)}</span>
          <span style={{ color: 'var(--text-dim)' }}>|</span>
          <span className="status-value">{t.lon.toFixed(5)}</span>
        </div>
      </div>
    </div>
  );
}

export default TopBar;

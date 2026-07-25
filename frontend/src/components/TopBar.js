import React from 'react';

// Flight-critical status only — view switching lives in the NavRail.
function TopBar({ telemetry, connected, backendUp, playback, onConnectClick }) {
  const t = telemetry;
  const gpsLabel = ['--', 'NO FIX', '2D', '3D', 'DGPS', 'RTK'][t.gps_fix] || `${t.gps_fix}`;
  const batteryColor = (t.battery_level ?? 100) < 20 ? 'var(--accent-red)' :
    (t.battery_level ?? 100) < 50 ? 'var(--accent-orange)' : 'var(--accent-green)';

  // M2: reflect the connection state machine + graded link level in the chip.
  const STATE_LABEL = {
    READY: 'LINKED', DEGRADED: 'DEGRADED', SYNCHRONIZING: 'SYNCING',
    WAITING_HEARTBEAT: 'WAITING', CONNECTING: 'CONNECTING', LOST: 'LINK LOST',
    DISCONNECTED: 'OFFLINE',
  };
  const LEVEL_DOT = { good: 'green', nominal: 'green', degraded: 'orange',
    poor: 'red', critical: 'red' };
  let linkLabel, linkDot;
  if (!backendUp) {
    linkLabel = 'NO BACKEND'; linkDot = 'orange';
  } else if (connected) {
    linkLabel = STATE_LABEL[t.link_state] || 'LINKED';
    linkDot = LEVEL_DOT[t.link_level] || 'green';
  } else {
    linkLabel = t.reconnecting ? 'RECONNECTING' : (STATE_LABEL[t.link_state] || 'OFFLINE');
    linkDot = t.reconnecting ? 'orange' : 'red';
  }
  const fw = t.capabilities?.fw_version;
  const linkTitle = connected
    ? `Connection settings\nstate: ${t.link_state}  ·  link: ${t.link_level}`
      + `\nGCS sysid ${t.gcs_sysid} → vehicle sysid ${t.vehicle_sysid ?? '?'}`
      + (fw ? `\nArduPilot ${fw}` : '')
      + (t.msgs_filtered ? `\nfiltered ${t.msgs_filtered} off-vehicle msgs` : '')
    : 'Connection settings';

  return (
    <div className="top-bar">
      <div className="top-bar-left">
        <span className="brand-title">GCS</span>
        <div className="status-chip" onClick={onConnectClick} style={{ cursor: 'pointer' }}
          title={linkTitle}>
          <span className={`dot ${linkDot}`}></span>
          <span>{linkLabel}</span>
        </div>
      </div>

      <div className="top-bar-center">
        <div className="mode-badge">{t.mode}</div>
        <div className={`armed-pill ${t.armed ? 'armed' : 'disarmed'}`}>
          {t.armed ? '● ARMED' : 'DISARMED'}
        </div>
        {playback && (
          <div className="status-chip" style={{ color: 'var(--accent-orange)' }}>
            <span className="dot orange"></span>
            <span>PLAYBACK</span>
          </div>
        )}
      </div>

      <div className="top-bar-right">
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

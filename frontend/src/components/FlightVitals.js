import React, { useState } from 'react';

const API = 'http://localhost:8000/api';

const fmtEta = (sec) => {
  const s = Math.round(sec);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};

// The in-flight supervision console: one calm vitals pill, mission progress,
// and the two intervention actions. Detail lives one tap away in the drawer.
function FlightVitals({ telemetry }) {
  const [drawer, setDrawer] = useState(false);
  const [busy, setBusy] = useState(false);
  const t = telemetry;

  const post = async (path, body) => {
    setBusy(true);
    try {
      await fetch(`${API}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
      });
    } catch { /* surfaced by status polling */ }
    setBusy(false);
  };

  const gs = t.groundspeed || 0;
  const eta = t.wp_dist > 5 && gs > 1 ? t.wp_dist / gs : null;
  const battWarn = t.battery_level !== null && t.battery_level < 25;
  const deg = (r) => (r * 57.2958).toFixed(0);

  return (
    <div className="flight-vitals">
      {drawer && (
        <div className="vitals-drawer glass-panel">
          <div className="vd-item"><span className="vd-label">PITCH</span><span className="vd-value">{deg(t.pitch)}°</span></div>
          <div className="vd-item"><span className="vd-label">ROLL</span><span className="vd-value">{deg(t.roll)}°</span></div>
          <div className="vd-item"><span className="vd-label">HDG</span><span className="vd-value">{t.heading}°</span></div>
          <div className="vd-item"><span className="vd-label">AIR SPD</span><span className="vd-value">{t.airspeed.toFixed(1)}</span></div>
          <div className="vd-item"><span className="vd-label">SATS</span><span className="vd-value">{t.gps_satellites}</span></div>
          <div className="vd-item"><span className="vd-label">BATT</span><span className="vd-value">{t.battery_voltage.toFixed(1)}V</span></div>
          <div className="vd-item"><span className="vd-label">CURR</span><span className="vd-value">{t.battery_current.toFixed(1)}A</span></div>
          <button
            className="control-btn danger vd-disarm"
            onClick={() => post('/vehicle/disarm', { force: true })}
            disabled={busy}
            title="Cuts the motor immediately"
          >
            DISARM
          </button>
        </div>
      )}

      {t.mission_seq > 0 && t.mission_count > 0 && (
        <div className="mission-progress">
          WP {Math.min(t.mission_seq, t.mission_count)}/{t.mission_count}
          {t.wp_dist > 1 && <> · {Math.round(t.wp_dist)} m</>}
          {eta && <> · ETA {fmtEta(eta)}</>}
        </div>
      )}

      <div className="vitals-row">
        <div
          className="vitals-pill"
          onClick={() => setDrawer((d) => !d)}
          title="Tap for details"
        >
          <div className="vital">
            <span className="vital-value">{t.altitude.toFixed(0)}</span>
            <span className="vital-label">ALT M</span>
          </div>
          <div className="vital">
            <span className="vital-value">{gs.toFixed(0)}</span>
            <span className="vital-label">SPD M/S</span>
          </div>
          <div className="vital">
            <span className="vital-value" style={battWarn ? { color: 'var(--accent-red)' } : undefined}>
              {t.battery_level !== null ? `${t.battery_level}%` : `${t.battery_voltage.toFixed(1)}V`}
            </span>
            <span className="vital-label">BATT</span>
          </div>
          <div className="vital vital-mode">{t.mode}</div>
        </div>

        <button className="vitals-btn rtl" onClick={() => post('/vehicle/mode', { mode: 'RTL' })} disabled={busy}>
          RTL
        </button>
        <button className="vitals-btn land" onClick={() => post('/vehicle/land')} disabled={busy}>
          LAND
        </button>
      </div>
    </div>
  );
}

export default FlightVitals;

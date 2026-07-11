import React, { useState } from 'react';

const API = 'http://localhost:8000/api';

function LaunchControl({ telemetry, connected }) {
  const [alt, setAlt] = useState(100);
  const [force, setForce] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(null); // { msg, kind }

  const flash = (msg, kind = 'info') => {
    setStatus({ msg, kind });
    setTimeout(() => setStatus(null), 6000);
  };

  const takeoff = async () => {
    setBusy(true);
    try {
      const res = await fetch(`${API}/vehicle/takeoff`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ alt: Number(alt), force }),
      });
      const data = await res.json();
      if (res.ok) {
        flash(`Launching — climbing to ${alt} m ✓`, 'ok');
      } else {
        // Surface the real reason (e.g. pre-arm checks) and hint at Force.
        const msg = data.detail || 'Takeoff failed';
        flash(msg + (!force && /arm/i.test(msg) ? ' — try Force arm' : ''), 'err');
      }
    } catch (e) {
      flash('Takeoff error — is the backend running?', 'err');
    }
    setBusy(false);
  };

  const land = async () => {
    setBusy(true);
    try {
      const res = await fetch(`${API}/vehicle/land`, { method: 'POST' });
      const data = await res.json();
      flash(res.ok ? 'Landing — flying approach to home ✓' : (data.detail || 'Landing failed'),
        res.ok ? 'ok' : 'err');
    } catch (e) {
      flash('Landing error', 'err');
    }
    setBusy(false);
  };

  const disarm = async () => {
    setBusy(true);
    try {
      await fetch(`${API}/vehicle/disarm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ force: true }),
      });
      flash('Disarmed', 'ok');
    } catch (e) {
      flash('Disarm error', 'err');
    }
    setBusy(false);
  };

  const gpsReady = telemetry.gps_fix >= 3;

  return (
    <div className="launch-control glass-panel">
      {!telemetry.armed ? (
        <div className="launch-row">
          <label className="launch-label">TAKEOFF ALT</label>
          <input
            type="number"
            value={alt}
            onChange={(e) => setAlt(e.target.value)}
            className="launch-alt"
            disabled={busy}
          />
          <span className="launch-unit">m</span>

          <label className={`force-toggle ${force ? 'on' : ''}`} title="Bypass pre-arm safety checks">
            <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
            ⚠ FORCE
          </label>

          <button
            className="control-btn success launch-btn"
            onClick={takeoff}
            disabled={!connected || busy || !gpsReady}
            title={!gpsReady ? 'Waiting for GPS 3D fix' : ''}
          >
            {busy ? 'LAUNCHING…' : 'ARM & TAKEOFF'}
          </button>
        </div>
      ) : (
        <div className="launch-row">
          <span className="airborne-status">
            <span className="dot green" /> AIRBORNE · {telemetry.mode} · {telemetry.altitude.toFixed(0)} m
          </span>
          <button className="control-btn success" onClick={land} disabled={busy}>
            LAND
          </button>
          <button className="control-btn danger" onClick={disarm} disabled={busy}>
            DISARM
          </button>
        </div>
      )}

      {!gpsReady && !telemetry.armed && (
        <div className="launch-hint">Waiting for GPS 3D fix…</div>
      )}
      {status && <div className={`launch-status ${status.kind}`}>{status.msg}</div>}
    </div>
  );
}

export default LaunchControl;

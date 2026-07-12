import React, { useState, useEffect } from 'react';

const API = 'http://localhost:8000/api';

function LaunchControl({ telemetry, connected }) {
  const [alt, setAlt] = useState(100);
  const [force, setForce] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(null); // { msg, kind }
  const [fenceOn, setFenceOn] = useState(null);      // null = unknown
  const [override, setOverride] = useState(false);   // bypass blocking checks
  const [showChecks, setShowChecks] = useState(true);

  // Geofence state feeds the pre-flight checklist (advisory item).
  useEffect(() => {
    if (!connected) { setFenceOn(null); return; }
    fetch(`${API}/safety/geofence`)
      .then((r) => (r.ok ? r.json() : null))
      .then((g) => setFenceOn(g ? !!g.enable : null))
      .catch(() => setFenceOn(null));
  }, [connected]);

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

  // Pre-flight checklist. Blockers gate ARM & TAKEOFF (override available);
  // advisories warn but never block — bench setups legitimately lack them.
  const t = telemetry;
  const rcSeen = (t.rc_channels || []).some((v) => v >= 900 && v <= 2100);
  const checks = [
    { label: 'Link', ok: connected, block: true },
    { label: 'GPS 3D fix', ok: gpsReady, block: true },
    { label: 'Home set', ok: t.home_lat !== 0 || t.home_lon !== 0, block: true },
    { label: 'Battery', ok: t.battery_level != null && t.battery_level > 30,
      warn: t.battery_level == null ? 'no data' : `${t.battery_level}%` },
    { label: 'RC input', ok: rcSeen, warn: 'none seen' },
    { label: 'Geofence', ok: fenceOn === true, warn: fenceOn == null ? 'unknown' : 'off' },
  ];
  const blockersPass = checks.filter((c) => c.block).every((c) => c.ok);
  const canLaunch = blockersPass || override;

  return (
    <div className="launch-control glass-panel">
      {!telemetry.armed && connected && (
        <div className="checklist">
          <div className="checklist-head" onClick={() => setShowChecks((s) => !s)}>
            <span>PRE-FLIGHT {blockersPass ? '✓' : '— NOT READY'}</span>
            <span className="checklist-chevron">{showChecks ? '▾' : '▸'}</span>
          </div>
          {showChecks && (
            <div className="checklist-items">
              {checks.map((c) => (
                <span key={c.label}
                  className={`check-item ${c.ok ? 'ok' : c.block ? 'bad' : 'warn'}`}>
                  {c.ok ? '✓' : c.block ? '✕' : '△'} {c.label}
                  {!c.ok && c.warn ? ` (${c.warn})` : ''}
                </span>
              ))}
              {!blockersPass && (
                <label className={`force-toggle ${override ? 'on' : ''}`}
                  title="Launch despite failed blocking checks">
                  <input type="checkbox" checked={override}
                    onChange={(e) => setOverride(e.target.checked)} />
                  OVERRIDE
                </label>
              )}
            </div>
          )}
        </div>
      )}
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
            disabled={!connected || busy || !canLaunch}
            title={!canLaunch ? 'Pre-flight checks not passed (override available)' : ''}
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

import React, { useState, useEffect } from 'react';

import { API } from '../api';

function LaunchControl({ telemetry, connected }) {
  const [alt, setAlt] = useState(100);
  const [force, setForce] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(null); // { msg, kind }
  const [override, setOverride] = useState(false);   // bypass blocking checks
  const [showChecks, setShowChecks] = useState(true);
  const [preflight, setPreflight] = useState(null);  // server verdicts (M6)

  // The checklist is EVALUATED by the backend (the same gate that refuses
  // /arm): this component only renders the verdicts, so what the operator
  // sees is exactly what the server enforces.
  useEffect(() => {
    if (!connected || telemetry.armed) { setPreflight(null); return; }
    let alive = true;
    const poll = async () => {
      try {
        const r = await fetch(`${API}/safety/preflight`);
        if (alive && r.ok) setPreflight(await r.json());
      } catch { if (alive) setPreflight(null); }
    };
    poll();
    const id = setInterval(poll, 2500);
    return () => { alive = false; clearInterval(id); };
  }, [connected, telemetry.armed]);

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
        // `override` mirrors the OVERRIDE toggle to the backend's M6 gate —
        // the server is the go/no-go authority now, not this checklist.
        body: JSON.stringify({ alt: Number(alt), force, override }),
      });
      const data = await res.json();
      if (res.ok) {
        flash(`Launching — climbing to ${alt} m ✓`, 'ok');
      } else {
        // Surface the real reason (e.g. pre-arm checks) and hint at Force.
        // The M6 gate returns structured detail {message, failed[]} — name the
        // failing blockers instead of a generic error.
        const d = data.detail;
        const msg = typeof d === 'string' ? d
          : d && d.message
            ? d.message + (d.failed ? ` — ${d.failed.join('; ')}` : '')
            : 'Takeoff failed';
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

  // Server verdicts when available; a minimal local fallback (link + GPS)
  // when the poll hasn't landed yet, so the panel is never blank.
  const checks = preflight
    ? preflight.checks.map((c) => ({
        label: c.label, ok: c.ok, block: c.blocker,
        warn: c.detail || undefined,
      }))
    : [
        { label: 'Link', ok: connected, block: true },
        { label: 'GPS 3D fix', ok: gpsReady, block: true },
      ];
  const blockersPass = preflight ? preflight.ready
    : checks.filter((c) => c.block).every((c) => c.ok);
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

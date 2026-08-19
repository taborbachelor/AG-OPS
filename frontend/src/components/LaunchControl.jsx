import React, { useState, useEffect } from 'react';

import { API } from '../api';

function LaunchControl({ telemetry, connected }) {
  const [alt, setAlt] = useState(100);
  const [force, setForce] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(null); // { msg, kind }
  const [override, setOverride] = useState(false);   // bypass blocking checks
  const [showChecks, setShowChecks] = useState(false);
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

  // ONE verdict, and it is the server's. There is deliberately no client-side
  // fallback: the old one computed "ready" from two locally-invented checks
  // (link + GPS) whenever the poll had not landed, so the panel could show a
  // pass the backend would refuse -- the exact UI-disagrees-with-the-server
  // failure M6 exists to remove. No verdict now renders as UNKNOWN, never as
  // a pass, and OVERRIDE remains available for the operator who needs it.
  const checks = preflight ? preflight.checks : [];
  const labelFor = (id) => {
    const c = checks.find((x) => x.id === id);
    return c ? c.label : id;
  };
  const verdict = !preflight ? 'unknown' : preflight.ready ? 'ready' : 'blocked';
  const failedBlockers = (preflight && preflight.failed_blockers) || [];
  const failedAdvisories = (preflight && preflight.advisories_failing) || [];

  // One plain sentence, assembled from the server's own verdict and labels --
  // no thresholds and no re-derivation of readiness happen here.
  const sentence =
    verdict === 'unknown'
      ? 'The pre-flight gate has not answered, so readiness is unknown.'
      : verdict === 'blocked'
        ? `Cannot arm: ${failedBlockers.map(labelFor).join(', ')}.`
        : failedAdvisories.length
          ? `Ready to fly. Not passing: ${failedAdvisories.map(labelFor).join(', ')}.`
          : 'Ready to fly. Every check passes.';

  const canLaunch = verdict === 'ready' || override;

  return (
    <div className="launch-control glass-panel">
      {!telemetry.armed && connected && (
        <div className="checklist">
          <div className={`pf-verdict pf-${verdict}`}>
            <span className="pf-state">
              {verdict === 'ready' ? 'READY'
                : verdict === 'blocked' ? 'NOT READY' : 'CHECKING…'}
            </span>
            <button type="button" className="pf-disclosure"
              aria-expanded={showChecks}
              onClick={() => setShowChecks((v) => !v)}>
              {showChecks ? '▾' : '▸'}{' '}
              {checks.length ? `${checks.length} checks` : 'detail'}
            </button>
          </div>

          <div className="pf-sentence">{sentence}</div>

          {/* An action, not detail -- it stays reachable while detail is closed. */}
          {verdict !== 'ready' && (
            <label className={`force-toggle ${override ? 'on' : ''}`}
              title="Launch despite failed blocking checks">
              <input type="checkbox" checked={override}
                onChange={(e) => setOverride(e.target.checked)} />
              OVERRIDE
            </label>
          )}

          {showChecks && (
            <div className="checklist-items">
              {checks.length === 0 && (
                <span className="check-item warn">No verdict from the server yet.</span>
              )}
              {checks.map((c) => (
                <span key={c.id}
                  className={`check-item ${c.ok ? 'ok' : c.blocker ? 'bad' : 'warn'}`}>
                  {c.ok ? '✓' : c.blocker ? '✕' : '△'} {c.label}
                  {!c.ok && c.detail ? ` (${c.detail})` : ''}
                </span>
              ))}
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

      {status && <div className={`launch-status ${status.kind}`}>{status.msg}</div>}
    </div>
  );
}

export default LaunchControl;

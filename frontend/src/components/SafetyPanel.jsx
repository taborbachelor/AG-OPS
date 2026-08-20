import React, { useState, useEffect, useCallback } from 'react';

import { API } from '../api';

const FENCE_ACTIONS = { 0: 'Report only', 1: 'RTL' };
const BATT_ACTIONS = { 0: 'None', 1: 'RTL', 2: 'Land' };
const GCS_ACTIONS = { 0: 'Disabled', 1: 'Enabled (RTL)' };
const RC_ACTIONS = { 0: 'Continue', 1: 'RTL' };

function Select({ label, value, options, onChange }) {
  return (
    <div className="safety-field">
      <span className="safety-label">{label}</span>
      <select value={value} onChange={(e) => onChange(Number(e.target.value))}>
        {Object.entries(options).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </div>
  );
}

function NumField({ label, value, unit, step = 1, onChange }) {
  return (
    <div className="safety-field">
      <span className="safety-label">{label}</span>
      <input type="number" step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))} style={{ width: 70 }} />
      <span className="safety-unit">{unit}</span>
    </div>
  );
}

// --- Read-back: what the vehicle ACTUALLY holds -----------------------------
// Four endpoints exist so the GCS can stop reporting what it last SENT and
// start reporting what the aircraft HOLDS. The distinction is the whole point:
// a send is not proof (the lesson M1b already paid for on parameter writes),
// and the two protections are not interchangeable -- the GCS-side proximity
// monitor dies with the radio link, the onboard exclusion fence does not.
//
// ONE INVARIANT GOVERNS EVERY STATE BELOW: an unknown must never render as an
// all-clear. `known: false`, `supported: false`, a disconnected link and a
// failed read are four different ways of NOT KNOWING, and every one of them is
// an operator being told "we cannot judge this" -- never "nothing to worry
// about". Zero points is only reportable as zero when we positively read zero.

export const UNKNOWN = 'unknown';   // we cannot judge -- say so
export const HELD = 'held';         // positively read, non-empty
export const CLEAR = 'clear';       // positively read, empty
export const INFO = 'info';         // a configured value, not a judgement
export const ALERT = 'alert';       // positively read, and bad

const LEVEL_COLOR = {
  [UNKNOWN]: 'var(--accent-orange)',
  [HELD]: 'var(--accent-green)',
  [CLEAR]: 'var(--text-secondary)',
  [INFO]: 'var(--text-secondary)',
  [ALERT]: 'var(--accent-red)',
};

/** One read, or null. A non-OK response and a dropped fetch are the same thing
 *  here -- we did not learn anything -- and null is what renders as UNKNOWN. */
async function getJson(url) {
  try {
    const r = await fetch(url);
    return r.ok ? await r.json() : null;
  } catch (e) { return null; }
}

/** The guardian's own verdict, rendered -- never recomputed here (M6: the UI
 *  holds no thresholds). DISARMED is a resting state, not a judgement. */
export function guardianStatus(cfg, state) {
  if (cfg.enabled === false) {
    return { level: UNKNOWN, text: 'DISABLED — nothing is being monitored' };
  }
  const s = state && state.state;
  if (!s) return { level: UNKNOWN, text: 'enabled — UNKNOWN, no state reported' };
  if (s === 'NORMAL') return { level: HELD, text: 'enabled — NORMAL' };
  if (s === 'DISARMED') return { level: INFO, text: 'enabled — DISARMED' };
  return { level: ALERT, text: `enabled — ${s}` };
}

/** The GCS-side proximity monitor (GET /safety/keepouts).
 *  Its own docstring: `known: false` means it cannot judge. */
export function monitorState(status) {
  if (!status) return { level: UNKNOWN, text: 'UNKNOWN — read failed' };
  if (status.known !== true) {
    return { level: UNKNOWN,
             text: 'UNKNOWN — not armed, so proximity is not being judged' };
  }
  const parts = [`${status.n_hazards} hazard rings`,
                 `${status.n_keepouts} keepouts`];
  if (status.hazard_buffer_m != null) parts.push(`${status.hazard_buffer_m} m buffer`);
  if (status.dropped) parts.push(`${status.dropped} dropped`);
  return { level: status.n_hazards > 0 ? HELD : CLEAR, text: parts.join(', ') };
}

/** A vehicle read-back (GET /safety/exclusions, GET /safety/rally).
 *  `connected` is load-bearing: both endpoints return an empty item list when
 *  there is no link, so a bare `points: 0` from a disconnected read is not
 *  evidence of an empty fence -- it is evidence of nothing at all. */
export function heldState(connected, res, noun) {
  if (!connected) {
    return { level: UNKNOWN, text: 'UNKNOWN — no link, cannot read the vehicle' };
  }
  if (!res) return { level: UNKNOWN, text: 'UNKNOWN — read-back failed' };
  if (res.supported === false) {
    return { level: UNKNOWN,
             text: `UNKNOWN — cannot read back: ${res.reason || 'transfer unsupported on this link'}` };
  }
  const n = Number(res.points || 0);
  if (n > 0) return { level: HELD, text: `${n} ${noun} held onboard` };
  return { level: CLEAR, text: 'none held onboard (read back from the vehicle)' };
}

/** Sent-vs-held: the GCS armed hazard rings, the aircraft holds no fence for
 *  them. Both halves are individually "fine"; together they mean the surveyed
 *  hazard is protected only while the radio is up. */
export function fenceMismatch(status, fence) {
  return Boolean(status && status.known === true && status.n_hazards > 0
                 && fence.level === CLEAR);
}

function ReadbackRow({ label, state }) {
  return (
    <div className="safety-field" style={{ alignItems: 'flex-start' }}>
      <span className="safety-label">{label}</span>
      <span style={{ flex: 1, fontSize: 11, color: LEVEL_COLOR[state.level] }}>
        {state.text}
      </span>
    </div>
  );
}

function Note({ children }) {
  return (
    <div style={{ fontSize: 10, color: 'var(--text-dim)', lineHeight: 1.4 }}>
      {children}
    </div>
  );
}

function SafetyPanel({ connected, fence, setFence }) {
  const [fs, setFs] = useState({
    batt_low_volt: 10.5, batt_low_action: 2, batt_crit_volt: 10.0, batt_crit_action: 1,
    gcs_enable: 1, rc_enable: true, rc_long_action: 1,
  });
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);
  // Everything the vehicle/monitor actually holds. `null` is NOT empty -- it is
  // "we have not read this", which renders as UNKNOWN. Rebuilt whole on every
  // read so a failed refresh can never leave a stale reading on screen looking
  // current.
  const [readback, setReadback] = useState({
    monitor: null, exclusions: null, rally: null, guardian: null,
  });

  const flash = (m) => { setStatus(m); setTimeout(() => setStatus(''), 4000); };

  const readAll = useCallback(async () => {
    if (!connected) return;
    setBusy(true);
    try {
      const [g, f] = await Promise.all([
        fetch(`${API}/safety/geofence`).then((r) => r.json()),
        fetch(`${API}/safety/failsafe`).then((r) => r.json()),
      ]);
      setFence({ enable: g.enable, radius: Math.round(g.radius), alt_max: Math.round(g.alt_max), action: g.action });
      setFs({
        batt_low_volt: +f.batt_low_volt.toFixed(1), batt_low_action: f.batt_low_action,
        batt_crit_volt: +f.batt_crit_volt.toFixed(1), batt_crit_action: f.batt_crit_action,
        gcs_enable: f.gcs_enable, rc_enable: f.rc_enable, rc_long_action: f.rc_long_action,
      });
      flash('Read current config from vehicle ✓');
    } catch (e) { flash('Read failed'); }
    setBusy(false);
  }, [connected, setFence]);

  // Auto-read once when the panel opens while connected.
  useEffect(() => { readAll(); }, [readAll]);

  const readBack = useCallback(async () => {
    // The monitor and the guardian are GCS-side, so they answer with no
    // vehicle. The fence and rally read-backs are not asked for at all while
    // disconnected -- they would answer `points: 0` from an empty link and
    // that number would be indistinguishable from a genuinely empty fence.
    const [monitor, guardian] = await Promise.all([
      getJson(`${API}/safety/keepouts`),
      getJson(`${API}/safety/guardian`),
    ]);
    let exclusions = null;
    let rally = null;
    if (connected) {
      [exclusions, rally] = await Promise.all([
        getJson(`${API}/safety/exclusions`),
        getJson(`${API}/safety/rally`),
      ]);
    }
    setReadback({ monitor, exclusions, rally, guardian });
  }, [connected]);

  useEffect(() => { readBack(); }, [readBack]);

  // Backend echo-verifies every param write (M1b): a 502 means the FC did not
  // confirm one or more values. Name them so the operator knows the fence/
  // failsafe is NOT set, instead of trusting a stale reading.
  const applyVerified = async (path, payload, label) => {
    setBusy(true);
    try {
      const res = await fetch(`${API}${path}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        flash(`${label} applied ✓`);
      } else {
        let failed = [];
        try { failed = (await res.json())?.detail?.failed || []; } catch (e) { /* no body */ }
        flash(failed.length
          ? `${label} NOT set — vehicle rejected ${failed.join(', ')}`
          : `${label} apply failed`);
      }
      await readAll();  // re-read so the UI shows what the FC actually holds
    } catch (e) { flash(`${label} error`); }
    setBusy(false);
  };

  const applyFence = () => applyVerified('/safety/geofence', fence, 'Geofence');
  const applyFailsafe = () => applyVerified('/safety/failsafe', fs, 'Failsafes');

  const setF = (patch) => setFence({ ...fence, ...patch });

  const monitor = monitorState(readback.monitor);
  const heldFence = heldState(connected, readback.exclusions, 'fence points');
  const heldRally = heldState(connected, readback.rally, 'rally points');
  const gCfg = readback.guardian && readback.guardian.config;
  const gState = readback.guardian && readback.guardian.state;

  return (
    <div className="safety-panel glass-panel">
      <div className="panel-title" style={{ marginBottom: 10 }}>Safety</div>

      <div className="safety-scroll">
        {/* GEOFENCE */}
        <div className="safety-card">
          <div className="safety-card-head">
            <span>GEOFENCE</span>
            <label className={`mini-toggle ${fence.enable ? 'on' : ''}`}>
              <input type="checkbox" checked={fence.enable}
                onChange={(e) => setF({ enable: e.target.checked })} />
              {fence.enable ? 'ON' : 'OFF'}
            </label>
          </div>
          <NumField label="Radius" value={fence.radius} unit="m" step={10}
            onChange={(v) => setF({ radius: v })} />
          <NumField label="Max alt" value={fence.alt_max} unit="m" step={5}
            onChange={(v) => setF({ alt_max: v })} />
          <Select label="On breach" value={fence.action} options={FENCE_ACTIONS}
            onChange={(v) => setF({ action: v })} />
          <button className="control-btn success" onClick={applyFence}
            disabled={!connected || busy} style={{ width: '100%', marginTop: 6 }}>
            Apply Geofence
          </button>
        </div>

        {/* FAILSAFES */}
        <div className="safety-card">
          <div className="safety-card-head"><span>FAILSAFES</span></div>
          <NumField label="Batt low" value={fs.batt_low_volt} unit="V" step={0.1}
            onChange={(v) => setFs({ ...fs, batt_low_volt: v })} />
          <Select label="→ action" value={fs.batt_low_action} options={BATT_ACTIONS}
            onChange={(v) => setFs({ ...fs, batt_low_action: v })} />
          <NumField label="Batt crit" value={fs.batt_crit_volt} unit="V" step={0.1}
            onChange={(v) => setFs({ ...fs, batt_crit_volt: v })} />
          <Select label="→ action" value={fs.batt_crit_action} options={BATT_ACTIONS}
            onChange={(v) => setFs({ ...fs, batt_crit_action: v })} />
          <Select label="GCS loss" value={fs.gcs_enable} options={GCS_ACTIONS}
            onChange={(v) => setFs({ ...fs, gcs_enable: v })} />
          <Select label="RC loss" value={fs.rc_long_action} options={RC_ACTIONS}
            onChange={(v) => setFs({ ...fs, rc_long_action: v })} />
          <button className="control-btn success" onClick={applyFailsafe}
            disabled={!connected || busy} style={{ width: '100%', marginTop: 6 }}>
            Apply Failsafes
          </button>
        </div>

        {/* WHAT THE GCS IS WATCHING -- soft, and only while the radio is up */}
        <div className="safety-card">
          <div className="safety-card-head">
            <span>PROXIMITY MONITOR</span>
            <button className="control-btn" onClick={readBack}
              style={{ padding: '2px 8px', fontSize: 10 }}>
              Re-read
            </button>
          </div>
          <ReadbackRow label="Armed with" state={monitor} />
          <Note>
            GCS-side. Warns on approach to a surveyed hazard — and stops the
            moment the link drops.
          </Note>
        </div>

        {/* WHAT THE AIRCRAFT HOLDS -- read off the FC, survives link loss */}
        <div className="safety-card">
          <div className="safety-card-head"><span>VEHICLE HOLDS</span></div>
          <ReadbackRow label="Excl. fence" state={heldFence} />
          <ReadbackRow label="Rally pts" state={heldRally} />
          {fenceMismatch(readback.monitor, heldFence) && (
            <div style={{ fontSize: 10, color: 'var(--accent-red)', lineHeight: 1.4 }}>
              SENT ≠ HELD — the GCS is armed with {readback.monitor.n_hazards} hazard
              rings the aircraft holds no exclusion fence for. Those hazards are
              protected only while the link is up.
            </div>
          )}
          <Note>
            Read back off the flight controller, not reported from what we sent.
            This is the protection that survives link loss.
          </Note>
        </div>

        {/* GUARDIAN -- GCS-side monitors. Read-only here; thresholds are set
            elsewhere, and this panel deliberately computes no verdicts of its
            own (M6: the UI renders the guardian's judgement, never its own). */}
        <div className="safety-card">
          <div className="safety-card-head"><span>GUARDIAN</span></div>
          {gCfg ? (
            <>
              <ReadbackRow label="Status" state={guardianStatus(gCfg, gState)} />
              <ReadbackRow label="Batt warn/RTL"
                state={{ level: INFO,
                         text: `${gCfg.batt_warn_volt} V / ${gCfg.batt_rtl_volt} V (${gCfg.batt_action})` }} />
              <ReadbackRow label="Bank warn"
                state={{ level: INFO,
                         text: `${gCfg.bank_warn_deg}° (${gCfg.bank_action}), tightened below ${gCfg.bank_low_alt_m} m` }} />
              <ReadbackRow label="Min sats"
                state={{ level: INFO, text: `${gCfg.gps_min_sats} (${gCfg.gps_action})` }} />
              <ReadbackRow label="Keepout"
                state={{ level: INFO,
                         text: `${gCfg.keepout_action} after ${gCfg.keepout_sustained_s} s` }} />
            </>
          ) : (
            <ReadbackRow label="Status"
              state={{ level: UNKNOWN, text: 'UNKNOWN — guardian read failed' }} />
          )}
        </div>

        <button className="control-btn" onClick={() => { readAll(); readBack(); }}
          disabled={busy} style={{ width: '100%' }}>
          Re-read from Vehicle
        </button>
      </div>

      {status && <div className="safety-status">{status}</div>}
      {!connected && <div className="safety-warn">Connect to a vehicle to read/apply</div>}
    </div>
  );
}

export default SafetyPanel;

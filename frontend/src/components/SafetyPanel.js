import React, { useState, useEffect, useCallback } from 'react';

const API = 'http://localhost:8000/api';

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

function SafetyPanel({ connected, fence, setFence }) {
  const [fs, setFs] = useState({
    batt_low_volt: 10.5, batt_low_action: 2, batt_crit_volt: 10.0, batt_crit_action: 1,
    gcs_enable: 1, rc_enable: true, rc_long_action: 1,
  });
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);

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

  const applyFence = async () => {
    setBusy(true);
    try {
      const res = await fetch(`${API}/safety/geofence`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(fence),
      });
      flash(res.ok ? 'Geofence applied ✓' : 'Geofence apply failed');
    } catch (e) { flash('Geofence error'); }
    setBusy(false);
  };

  const applyFailsafe = async () => {
    setBusy(true);
    try {
      const res = await fetch(`${API}/safety/failsafe`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(fs),
      });
      flash(res.ok ? 'Failsafes applied ✓' : 'Failsafe apply failed');
    } catch (e) { flash('Failsafe error'); }
    setBusy(false);
  };

  const setF = (patch) => setFence({ ...fence, ...patch });

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

        <button className="control-btn" onClick={readAll} disabled={!connected || busy}
          style={{ width: '100%' }}>
          Re-read from Vehicle
        </button>
      </div>

      {status && <div className="safety-status">{status}</div>}
      {!connected && <div className="safety-warn">Connect to a vehicle to read/apply</div>}
    </div>
  );
}

export default SafetyPanel;

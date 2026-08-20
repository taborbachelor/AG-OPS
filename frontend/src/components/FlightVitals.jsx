import React, { useRef, useState } from 'react';

import { API } from '../api';

const fmtEta = (sec) => {
  const s = Math.round(sec);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};

// --- Live guardian monitor readouts ----------------------------------------
// The guardian evaluates nine monitors on every telemetry frame and ships the
// whole verdict tree in `telemetry.guardian.monitors`. Until now nothing
// rendered it, so a monitor was invisible until it had ALREADY tripped: the
// operator saw "hazard 12 m away" as a breach banner, never the 400 -> 200 ->
// 60 m approach that led to it. These rows are that approach.
//
// M6 IS ABSOLUTE HERE: this file holds no thresholds and computes no verdicts.
// Every ok/not-ok below is the guardian's own `ok` flag, and every limit shown
// is the guardian's own number. We render its judgement or admit we have none.
//
// AND `ok: true` IS NOT ALWAYS A PASS. Two ways it lies if rendered green:
//   - Every monitor only judges while ARMED. On the ground all nine report
//     ok:true, having judged nothing at all.
//   - `rtl_margin.ok` defaults to TRUE when the margin cannot be computed --
//     no pack capacity configured, no current draw, no home. A green return
//     margin that was never estimated is the worst cell on this panel.
// So a monitor that is not judging reads UNKNOWN, never OK.

export const OK = 'ok';
export const ALERT = 'alert';
export const UNKNOWN = 'unknown';

const LEVEL_COLOR = {
  [OK]: 'var(--accent-green)',
  [ALERT]: 'var(--accent-red)',
  [UNKNOWN]: 'var(--accent-orange)',
};

/** The guardian's verdict, or UNKNOWN when it was not in a position to judge.
 *  `judging` is the monitor's own precondition (armed, airborne, computable). */
function verdict(ok, judging) {
  if (!judging) return UNKNOWN;
  return ok ? OK : ALERT;
}

const n1 = (v) => (typeof v === 'number' ? v.toFixed(1) : null);

/** Flatten guardian.monitors into display rows. Pure: telemetry in, rows out. */
export function monitorRows(guardian, armed) {
  const m = guardian && guardian.monitors;
  if (!m || !Object.keys(m).length) {
    return [{ key: 'none', label: 'MONITORS', level: UNKNOWN,
              value: 'UNKNOWN — no guardian verdicts in telemetry' }];
  }
  const rows = [];
  const flying = Boolean(armed);

  // Hazard proximity. `known` is reported separately from `ok` by the backend
  // on purpose: no ring data means it cannot judge, which must never be a tick.
  const k = m.keepout || {};
  rows.push({
    key: 'keepout',
    label: 'HAZARD',
    level: !k.known ? UNKNOWN : verdict(k.ok, flying),
    value: k.hazard_dist_m != null
      ? `${k.hazard_dist_m} m to ${k.hazard_kind || 'hazard'}`
      : (k.known ? 'none within range' : '—'),
    note: !k.known
      ? 'no rings loaded — proximity is not being judged'
      : (k.keepout_complete === false
        ? 'ring set truncated — the keepout distance is a subset answer'
        : (flying ? null : 'not judged until armed')),
  });

  // RTL energy margin: seconds of pack left, minus seconds to fly home, minus
  // the landing reserve. Null margin means it could not be worked out.
  const r = m.rtl_margin || {};
  const estimated = r.margin_s != null;
  rows.push({
    key: 'rtl_margin',
    label: 'RTL MARGIN',
    level: estimated ? verdict(r.ok, flying) : UNKNOWN,
    value: estimated
      ? `${r.margin_s}s spare — ${r.time_left_s}s pack vs ${r.time_home_s}s home`
      : 'no estimate',
    note: estimated
      ? (flying ? null : 'not judged until armed')
      : 'needs pack capacity, a current draw and a home fix',
  });

  // Bank and airspeed share the guardian's airborne gate.
  const a = m.airspeed || {};
  const airborne = Boolean(a.airborne);
  const b = m.bank || {};
  rows.push({
    key: 'bank',
    label: 'BANK',
    level: verdict(b.ok, flying && airborne),
    value: b.roll_deg != null ? `${b.roll_deg}° of ${b.limit_deg}°` : '—',
    note: !airborne ? 'on the ground — not judged'
      : (b.low_alt ? 'limit tightened: low altitude' : null),
  });
  rows.push({
    key: 'airspeed',
    label: 'AIRSPEED',
    level: verdict(a.ok, flying && airborne),
    value: a.airspeed != null ? `${a.airspeed} m/s` : '—',
    note: airborne ? null : 'on the ground — not judged',
  });

  const v = m.vibration || {};
  rows.push({
    key: 'vibration',
    label: 'VIBRATION',
    level: verdict(v.ok, flying),
    value: v.peak_ms2 != null
      ? `${v.peak_ms2} m/s² peak · ${v.new_clips || 0} clips`
      : '—',
    note: flying ? null : 'not judged until armed',
  });

  const e = m.ekf || {};
  rows.push({
    key: 'ekf',
    label: 'EKF',
    level: verdict(e.ok, flying),
    value: e.pos_var != null
      ? `pos ${e.pos_var} · vel ${e.vel_var}${e.healthy === false ? ' · UNHEALTHY' : ''}`
      : '—',
    note: flying ? null : 'not judged until armed',
  });

  const g = m.gps || {};
  rows.push({
    key: 'gps',
    label: 'GPS',
    level: verdict(g.ok, flying),
    value: g.sats != null ? `${g.sats} sats · fix ${g.fix}` : '—',
    note: flying ? null : 'not judged until armed',
  });

  const bat = m.battery || {};
  rows.push({
    key: 'battery',
    label: 'PACK',
    level: verdict(bat.ok, flying),
    value: n1(bat.volts) != null ? `${n1(bat.volts)} V` : '—',
    note: flying ? null : 'not judged until armed',
  });

  const l = m.link || {};
  rows.push({
    key: 'link',
    label: 'LINK',
    level: l.level == null ? UNKNOWN : verdict(l.ok, flying),
    value: l.level || 'unknown',
    note: flying ? null : 'not judged until armed',
  });

  return rows;
}

/** Colour for the HOME pill. The guardian's energy margin, or NO colour --
 *  never a green borrowed from a battery percentage this file invented. */
export function homeMarginLevel(guardian, armed) {
  const r = (guardian && guardian.monitors && guardian.monitors.rtl_margin) || {};
  if (r.margin_s == null || !armed) return null;
  return r.ok ? OK : ALERT;
}

// The in-flight supervision console: one calm vitals pill, mission progress,
// and the two intervention actions. Detail lives one tap away in the drawer.
function FlightVitals({ telemetry }) {
  const [drawer, setDrawer] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const noteTimer = useRef(null);
  const t = telemetry;

  const fail = (m) => {
    setNote(m);
    clearTimeout(noteTimer.current);
    noteTimer.current = setTimeout(() => setNote(''), 8000);
  };

  // In-flight commands must never fail silently: the status poll only tracks
  // link state, not command acks, so a rejected RTL/LAND/DISARM has to be
  // surfaced right here where the operator clicked it.
  const post = async (path, body, label) => {
    setBusy(true);
    try {
      const res = await fetch(`${API}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!res.ok) {
        let detail = '';
        try { detail = (await res.json()).detail; } catch { /* no body */ }
        fail(`${label} FAILED — ${detail || `HTTP ${res.status}`}`);
      }
    } catch {
      fail(`${label} FAILED — no response from backend`);
    }
    setBusy(false);
  };

  const gs = t.groundspeed || 0;
  const eta = t.wp_dist > 5 && gs > 1 ? t.wp_dist / gs : null;
  const battWarn = t.battery_level !== null && t.battery_level < 25;
  const deg = (r) => (r * 57.2958).toFixed(0);

  // Distance and ETA home are geometry, not a verdict, so they stay here.
  const homeSet = t.home_lat !== 0 || t.home_lon !== 0;
  const kxh = 111320 * Math.cos(((t.lat || 0) * Math.PI) / 180);
  const distHome = homeSet && t.lat
    ? Math.hypot((t.lat - t.home_lat) * 111320, (t.lon - t.home_lon) * kxh)
    : null;
  const etaHome = distHome != null && gs > 2 ? distHome / gs : null;
  // The COLOUR is a verdict, and it now comes from the guardian's RTL energy
  // margin rather than the battery-percent rule this file used to invent.
  // The two disagreed by construction: a 60% pack 4 km downwind was green.
  // No estimate means no colour -- M6 forbids the "sensible default" that
  // would paint an unjudged margin green.
  const guardian = t.guardian || {};
  const marginLevel = homeMarginLevel(guardian, t.armed);
  const marginColor = marginLevel ? LEVEL_COLOR[marginLevel] : undefined;
  const rows = monitorRows(guardian, t.armed);

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
          <div className="vd-monitors">
            <div className="vd-monitors-head">GUARDIAN MONITORS</div>
            {rows.map((r) => (
              <div key={r.key} className="vd-monitor">
                <span className="vd-label">{r.label}</span>
                <span className="vd-monitor-value" style={{ color: LEVEL_COLOR[r.level] }}>
                  {r.level === UNKNOWN ? `${r.value} · UNKNOWN` : r.value}
                </span>
                {r.note && <span className="vd-monitor-note">{r.note}</span>}
              </div>
            ))}
          </div>

          <button
            className="control-btn danger vd-disarm"
            onClick={() => post('/vehicle/disarm', { force: true }, 'DISARM')}
            disabled={busy}
            title="Cuts the motor immediately"
          >
            DISARM
          </button>
        </div>
      )}

      {(() => {
        // Guardian verdicts ride in telemetry: show the emergency state and
        // its reason whenever the GCS-side failsafe layer has something to
        // say. RTL states are red; plain monitor warnings are amber.
        const g = guardian;
        if (!g.state || g.state === 'NORMAL' || g.state === 'DISARMED') return null;
        const red = g.state === 'RTL_REQUESTED' || g.state === 'RTL_ACTIVE';
        const detail = g.rtl_reason || (g.warnings && g.warnings[0]) || '';
        return (
          <div className={`guardian-chip ${red ? 'red' : 'amber'}`}>
            GUARDIAN {g.state.replace('_', ' ')}{detail ? ` — ${detail}` : ''}
          </div>
        );
      })()}

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
          {distHome != null && (
            <div className="vital">
              <span className="vital-value" style={marginColor ? { color: marginColor } : undefined}>
                {distHome >= 1000 ? `${(distHome / 1000).toFixed(1)}k` : Math.round(distHome)}
              </span>
              <span className="vital-label">
                HOME M{etaHome != null ? ` · ${fmtEta(etaHome)}` : ''}
              </span>
            </div>
          )}
          <div className="vital vital-mode">{t.mode}</div>
        </div>

        <button className="vitals-btn rtl" onClick={() => post('/vehicle/mode', { mode: 'RTL' }, 'RTL')} disabled={busy}>
          RTL
        </button>
        <button className="vitals-btn land" onClick={() => post('/vehicle/land', null, 'LAND')} disabled={busy}>
          LAND
        </button>
      </div>

      {note && <div className="vitals-note">{note}</div>}
    </div>
  );
}

export default FlightVitals;

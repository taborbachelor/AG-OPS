import React, { useState } from 'react';

/**
 * Post-flight scorecard (backend Part 3B), rendered read-only.
 *
 * The backend writes one beside every flight log on disarm and serves it on
 * GET /api/logs/{name}. Until this component existed, no operator could see
 * it without hitting the API by hand — the same seam class as the 86c6a6e
 * keepout-arming bug: a complete backend surface with no caller.
 *
 * Two invariants are inherited from the writer and must not be softened here:
 *
 *  1. Every extreme is null rather than 0 when it was never measured. "No
 *     wind data this flight" and "zero wind this flight" are different facts,
 *     and rendering a missing nearest-powerline distance as 0 m — or as a
 *     green tick — would be a dangerous lie. Missing renders as a dash.
 *  2. A missing scorecard means NOT AVAILABLE, never "nothing to report".
 *
 * It also holds NO thresholds. The scorecard JSON carries none, and M6 moved
 * in-flight threshold judgement to the guardian precisely so the UI could not
 * disagree with it. So values are shown plainly and the only thing coloured
 * is the guardian's own verdict: its per-monitor warning counts.
 */

const MONITOR_LABELS = {
  link: 'Link',
  gps: 'GPS',
  battery: 'Battery',
  rtl_margin: 'RTL margin',
  ekf: 'EKF',
  vibration: 'Vibration',
  airspeed: 'Airspeed',
  bank: 'Bank angle',
  keepout: 'Keepout proximity',
};

// [key, label, unit, decimal places]
const APPROACHES = [
  ['min_hazard_dist_m', 'Nearest hazard', 'm', 1],
  ['min_keepout_dist_m', 'Nearest keepout', 'm', 1],
  ['min_rtl_margin_s', 'Min RTL margin', 's', 0],
];

const EXTREMES = [
  ['max_bank_deg', 'Max bank', '\u00b0', 1],
  ['min_airspeed_ms', 'Min airspeed', 'm/s', 1],
  ['max_wind_ms', 'Max wind', 'm/s', 1],
  ['min_battery_v', 'Min battery', 'V', 2],
  ['max_ekf_pos_var', 'Max EKF pos var', '', 2],
  ['max_ekf_vel_var', 'Max EKF vel var', '', 2],
  ['max_vibe_ms2', 'Max vibration', 'm/s\u00b2', 2],
  ['clip_events', 'Peak accel clips', '', 0],
];

const fmtDur = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

function Metric({ label, value, unit, dp }) {
  const missing = value === null || value === undefined;
  return (
    <div className={`sc-metric${missing ? ' sc-metric-missing' : ''}`}>
      <span className="sc-label">{label}</span>
      <span className="sc-value">
        {missing ? (
          <span title="Not recorded on this flight">&mdash;</span>
        ) : (
          <>
            {Number(value).toFixed(dp)}
            {unit ? <span className="sc-unit">{unit}</span> : null}
          </>
        )}
      </span>
    </div>
  );
}

function Scorecard({ card }) {
  const [showAll, setShowAll] = useState(false);

  // Absent is a distinct state, not a clean flight. Say which.
  if (!card) {
    return (
      <section className="scorecard" aria-label="Post-flight scorecard">
        <div className="sc-title">Post-flight scorecard</div>
        <div className="sc-absent">
          Not available for this flight. One is written when the aircraft disarms,
          so flights recorded before scorecards existed &mdash; or that the backend
          never saw disarm &mdash; have none. This is not a clean-flight result.
        </div>
      </section>
    );
  }

  const warnings = card.warnings || {};
  const warned = Object.entries(warnings)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1]);

  return (
    <section className="scorecard" aria-label="Post-flight scorecard">
      <div className="sc-title">Post-flight scorecard</div>

      <div className="sc-head">
        {typeof card.duration_s === 'number' ? fmtDur(card.duration_s) : '\u2014'}
        {' \u00b7 '}
        {card.samples ?? 0} guardian ticks
      </div>

      <div className="sc-section">Closest approaches</div>
      {APPROACHES.map(([k, label, unit, dp]) => (
        <Metric key={k} label={label} value={card[k]} unit={unit} dp={dp} />
      ))}

      <div className="sc-section">
        Guardian warnings
        <span className="sc-hint" title="Counted once per episode on the rising edge, not once per tick">
          episodes
        </span>
      </div>
      {warned.length === 0 ? (
        <div className="sc-clean">No monitor raised a warning.</div>
      ) : (
        warned.map(([k, n]) => (
          <div key={k} className="sc-metric sc-metric-warn">
            <span className="sc-label">{MONITOR_LABELS[k] || k}</span>
            <span className="sc-value">{n}&times;</span>
          </div>
        ))
      )}

      <button
        type="button"
        className="sc-toggle"
        aria-expanded={showAll}
        onClick={() => setShowAll((v) => !v)}
      >
        {showAll ? '\u25be' : '\u25b8'} Flight extremes
      </button>
      {showAll && (
        <div className="sc-extremes">
          {EXTREMES.map(([k, label, unit, dp]) => (
            <Metric key={k} label={label} value={card[k]} unit={unit} dp={dp} />
          ))}
        </div>
      )}
    </section>
  );
}

export default Scorecard;

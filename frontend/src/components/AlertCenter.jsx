import React, { useState, useEffect, useRef } from 'react';

// Aviation-style annunciator: the screen stays quiet until something needs
// the operator, then a color-coded banner appears (and optionally speaks).
// Each rule is evaluated on every telemetry tick; an alert speaks once per
// activation and clears itself when its condition resolves.
//
// THE BACKEND IS THE AUTHORITY (M6 alert-threshold unification). Every
// in-flight condition is rendered from the guardian's own verdicts —
// `telemetry.guardian.warning_items`, one banner per warning, keyed by the
// monitor that raised it. This component holds NO in-flight thresholds of its
// own; it used to carry its own battery-percent and GPS-fix rules, which meant
// two sources of truth for "is the battery low" that could disagree (the
// guardian judges on VOLTAGE, this judged on percent) and one that ignored the
// sat-count limit entirely. Same pattern LaunchControl already follows for
// server preflight verdicts.
//
// What legitimately stays client-side, and why:
//   - LINK LOST / RECONNECTING: when telemetry stops, guardian verdicts are
//     frozen at their last value. Only the client knows the link is gone.
//   - RTL ENGAGED: vehicle-truth mode change, not a guardian verdict.
//   - Pre-arm battery hint: the guardian deliberately stays silent while
//     disarmed (a bench vehicle on a flat pack is normal). This is an
//     advisory only — the real pre-arm gate is the server preflight check.

// Pre-arm pack advisory only. NOT an in-flight threshold — the guardian owns
// those, on voltage. Deliberately not sourced from guardian config: that config
// has no percentage threshold, and inventing one on the backend purely to feed
// a UI hint would recreate the duplication this unification removed.
const PREARM_BATT_PCT = 25;

// Voice callouts per guardian monitor. The banner always shows the backend's
// full warning text; speech has to be short enough to be useful in the air.
const SPOKEN = {
  link: 'Link degraded',
  gps: 'GPS degraded',
  battery: 'Battery low',
  rtl_margin: 'Return margin low',
  ekf: 'Navigation degraded',
  vibration: 'Vibration high',
  airspeed: 'Airspeed low',
  bank: 'Bank angle',
  keepout: 'Hazard proximity',
};

// --- The vehicle's own messages --------------------------------------------
// ArduPilot talks: "PreArm: Radio failsafe on", "Polygon fence breached",
// "EKF variance", "Throttle failsafe on". Every one of those is the flight
// controller telling the operator something no GCS-side monitor can know, and
// until now they were parsed, ring-buffered, logged -- and never displayed.
// The backend keeps the last five in `telemetry.statustext`.
//
// Deliberately NOT spoken and NOT dismissible. These are a feed, not an
// annunciator: the guardian's warnings above are judged conditions that clear
// themselves, while this is a transcript. Speaking it would put ArduPilot's
// routine chatter over the top of the callouts that matter.
//
// MAV severities 0-7 are EMERGENCY..DEBUG; 0-3 are real failures, 4 is a
// warning, the rest are informational.
const SEV_CLASS = (sev) => (sev <= 3 ? 'red' : sev === 4 ? 'amber' : 'info');

/** Newest first, and never presented as live when the link is down: with no
 *  telemetry these five are the last thing we heard, not the current state. */
export function statusFeed(telemetry, connected) {
  const raw = (telemetry && telemetry.statustext) || [];
  const items = raw.slice().reverse().map((m, i) => ({
    key: `${m.t}:${i}`,
    sev: SEV_CLASS(m.severity),
    severity_name: m.severity_name,
    text: m.text,
  }));
  return { items, stale: !connected && items.length > 0 };
}

const speak = (text) => {
  try {
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.0;
    window.speechSynthesis.speak(u);
  } catch { /* voice is best-effort */ }
};

function AlertCenter({ telemetry, connected, reconnecting }) {
  const [voiceOn, setVoiceOn] = useState(() => localStorage.getItem('gcs_voice') !== 'off');
  const [dismissed, setDismissed] = useState({});   // id -> true until condition clears
  const [active, setActive] = useState([]);         // [{id, sev, text}]
  const spokenRef = useRef({});                     // id -> spoke for current activation
  const wasArmedRef = useRef(false);                // link-loss context survives telemetry reset
  const prevModeRef = useRef('UNKNOWN');
  const rtlLatchRef = useRef(false);

  useEffect(() => {
    const t = telemetry;
    if (connected && t.armed) wasArmedRef.current = true;
    if (connected && !t.armed) wasArmedRef.current = false;

    // RTL engaged mid-flight (failsafe or operator) — worth a callout either way.
    if (t.mode === 'RTL' && prevModeRef.current !== 'RTL' && t.armed) {
      rtlLatchRef.current = true;
    }
    if (t.mode !== 'RTL' || !t.armed) rtlLatchRef.current = false;
    prevModeRef.current = t.mode;

    const batt = t.battery_level;
    const guardian = t.guardian || {};
    const rules = [
      { id: 'link', sev: 'red', on: wasArmedRef.current && !connected && !reconnecting,
        text: 'LINK LOST', say: 'Link lost' },
      { id: 'reconn', sev: 'amber', on: !!reconnecting,
        text: 'LINK DOWN — RECONNECTING', say: 'Link down, reconnecting' },
      // Pre-arm advisory ONLY (see header). While armed the guardian's
      // voltage-based battery monitor owns this, and firing both would show
      // the operator two different battery verdicts at once.
      { id: 'battPre', sev: 'amber',
        on: connected && !t.armed && batt != null && batt < PREARM_BATT_PCT,
        text: `PACK LOW — ${batt}% (pre-arm)`, say: 'Pack low' },
      { id: 'rtl', sev: 'amber', on: rtlLatchRef.current,
        text: 'RTL ENGAGED — RETURNING HOME', say: 'Returning to launch' },
      // A guardian-commanded RTL, with the condition it recorded.
      { id: 'guardR', sev: 'red',
        on: connected && !!guardian.rtl_source,
        text: `GUARDIAN RTL — ${guardian.rtl_reason || 'commanded return'}`,
        say: 'Guardian returning home' },
    ];

    // One banner per active guardian warning, keyed by its monitor so each
    // keeps its own dismiss/spoken state and a second concurrent warning is
    // never hidden behind the first.
    if (connected) {
      for (const w of guardian.warning_items || []) {
        rules.push({
          id: `guard:${w.monitor}`,
          sev: 'amber',
          on: true,
          text: `GUARDIAN — ${w.text}`,
          say: SPOKEN[w.monitor] || 'Guardian warning',
        });
      }
    }

    // Guardian rules only EXIST while their warning is active, so a cleared
    // warning never reaches the `else` branch below that resets per-alert
    // state. Without this, a bank warning that clears and returns would never
    // speak again, and a dismissed one would stay dismissed forever.
    const liveGuardIds = new Set(rules.filter((r) => r.id.startsWith('guard:'))
      .map((r) => r.id));
    for (const id of Object.keys(spokenRef.current)) {
      if (id.startsWith('guard:') && !liveGuardIds.has(id)) {
        delete spokenRef.current[id];
      }
    }
    const staleDismissed = Object.keys(dismissed)
      .filter((id) => id.startsWith('guard:') && !liveGuardIds.has(id));
    if (staleDismissed.length) {
      setDismissed((d) => {
        const c = { ...d };
        staleDismissed.forEach((id) => delete c[id]);
        return c;
      });
    }

    const next = [];
    for (const r of rules) {
      if (r.on) {
        if (!dismissed[r.id]) next.push({ id: r.id, sev: r.sev, text: r.text });
        if (!spokenRef.current[r.id]) {
          spokenRef.current[r.id] = true;
          if (voiceOn) speak(r.say);
        }
      } else {
        spokenRef.current[r.id] = false;
        if (dismissed[r.id]) {
          setDismissed((d) => { const c = { ...d }; delete c[r.id]; return c; });
        }
      }
    }
    setActive((prev) => {
      // Avoid re-render churn when nothing changed.
      const same = prev.length === next.length
        && prev.every((p, i) => p.id === next[i].id && p.text === next[i].text);
      return same ? prev : next;
    });
  }, [telemetry, connected, reconnecting, voiceOn, dismissed]);

  const feed = statusFeed(telemetry, connected);

  const toggleVoice = () => {
    setVoiceOn((v) => {
      localStorage.setItem('gcs_voice', v ? 'off' : 'on');
      return !v;
    });
  };

  return (
    <div className="alert-center">
      {active.map((a) => (
        <div key={a.id} className={`alert-banner ${a.sev}`}>
          <span className="alert-text">{a.text}</span>
          <button className="alert-x" title="Dismiss"
            onClick={() => setDismissed((d) => ({ ...d, [a.id]: true }))}>×</button>
        </div>
      ))}
      {feed.items.length > 0 && (
        <div className="statustext-feed">
          <div className="stf-head">
            VEHICLE MESSAGES
            {feed.stale && <span className="stf-stale"> — LAST HEARD, LINK DOWN</span>}
          </div>
          {feed.items.map((m) => (
            <div key={m.key} className={`stf-line ${m.sev}`}>
              <span className="stf-sev">{m.severity_name}</span>
              <span className="stf-text">{m.text}</span>
            </div>
          ))}
        </div>
      )}

      <button
        className={`voice-toggle ${voiceOn ? 'on' : ''}`}
        onClick={toggleVoice}
        title={voiceOn ? 'Voice callouts on' : 'Voice callouts off'}
      >
        {voiceOn ? '🔊' : '🔇'}
      </button>
    </div>
  );
}

export default AlertCenter;

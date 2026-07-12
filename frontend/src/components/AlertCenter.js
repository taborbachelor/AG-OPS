import React, { useState, useEffect, useRef } from 'react';

// Aviation-style annunciator: the screen stays quiet until something needs
// the operator, then a color-coded banner appears (and optionally speaks).
// Each rule is evaluated on every telemetry tick; an alert speaks once per
// activation and clears itself when its condition resolves.

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
    const rules = [
      { id: 'link', sev: 'red', on: wasArmedRef.current && !connected && !reconnecting,
        text: 'LINK LOST', say: 'Link lost' },
      { id: 'reconn', sev: 'amber', on: !!reconnecting,
        text: 'LINK DOWN — RECONNECTING', say: 'Link down, reconnecting' },
      { id: 'battR', sev: 'red', on: connected && batt != null && batt < 15,
        text: `BATTERY CRITICAL — ${batt}%`, say: 'Battery critical' },
      { id: 'battA', sev: 'amber', on: connected && batt != null && batt >= 15 && batt < 25,
        text: `BATTERY LOW — ${batt}%`, say: 'Battery low' },
      { id: 'gps', sev: 'amber', on: connected && t.armed && t.gps_fix < 3,
        text: 'GPS DEGRADED', say: 'GPS degraded' },
      { id: 'rtl', sev: 'amber', on: rtlLatchRef.current,
        text: 'RTL ENGAGED — RETURNING HOME', say: 'Returning to launch' },
    ];

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

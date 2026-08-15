import React from 'react';

// ArduPlane default output mapping (SERVOn_FUNCTION): 1=Aileron 2=Elevator
// 3=Throttle 4=Rudder. We visualize those four; a raw list shows all 8.
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const defl = (pwm) => clamp((pwm - 1500) / 500, -1, 1);      // -1..1
const thr = (pwm) => clamp((pwm - 1000) / 1000, 0, 1);        // 0..1

const SURFACE = '#1b2a3d';
const ACTIVE = '#00e5ff';
const surfFill = (d) => (Math.abs(d) > 0.03 ? ACTIVE : SURFACE);

function IOEntry({ label, inv, outv }) {
  return (
    <div className="io-row">
      <span className="io-label">{label}</span>
      <span className="io-in">{inv || '--'}</span>
      <span className="io-arrow">→</span>
      <span className="io-out">{outv || '--'}</span>
    </div>
  );
}

function ControlsPanel({ telemetry }) {
  const out = telemetry.servo_outputs || [];
  const rc = telemetry.rc_channels || [];
  const have = out.length >= 4;

  const ail = defl(out[0] || 1500);
  const elev = defl(out[1] || 1500);
  const throttle = thr(out[2] || 1000);
  const rud = defl(out[3] || 1500);

  const AIL = 22, ELEV = 20, RUD = 26; // max degrees for the animation
  // Spin faster with throttle; stop at idle.
  const spin = throttle > 0.02 ? (2.2 - throttle * 2.05).toFixed(2) : 0;

  return (
    <div className="controls-panel glass-panel">
      <div className="panel-title" style={{ marginBottom: 6 }}>Control Surfaces</div>

      <svg viewBox="0 0 260 236" className="aircraft-svg">
        {/* Wings */}
        <rect x="24" y="98" width="212" height="26" rx="6" fill="#12202f" stroke="#24374d" />
        {/* Fuselage */}
        <rect x="122" y="26" width="16" height="184" rx="8" fill="#16273a" stroke="#294159" />
        {/* Nose cone */}
        <polygon points="130,14 122,30 138,30" fill="#294159" />

        {/* Left aileron (hinge at inner end) */}
        <g style={{ transform: `rotate(${-ail * AIL}deg)`, transformOrigin: '108px 120px',
                    transition: 'transform 0.06s linear' }}>
          <rect x="40" y="118" width="68" height="9" rx="2"
            fill={surfFill(ail)} stroke="#0a1420" />
        </g>
        {/* Right aileron */}
        <g style={{ transform: `rotate(${ail * AIL}deg)`, transformOrigin: '152px 120px',
                    transition: 'transform 0.06s linear' }}>
          <rect x="152" y="118" width="68" height="9" rx="2"
            fill={surfFill(ail)} stroke="#0a1420" />
        </g>

        {/* Horizontal stabilizer */}
        <rect x="92" y="182" width="76" height="16" rx="4" fill="#12202f" stroke="#24374d" />
        {/* Elevator */}
        <g style={{ transform: `rotate(${-elev * ELEV}deg)`, transformOrigin: '130px 194px',
                    transition: 'transform 0.06s linear' }}>
          <rect x="94" y="194" width="72" height="8" rx="2"
            fill={surfFill(elev)} stroke="#0a1420" />
        </g>

        {/* Rudder (swings left/right at the tail) */}
        <g style={{ transform: `rotate(${rud * RUD}deg)`, transformOrigin: '130px 202px',
                    transition: 'transform 0.06s linear' }}>
          <polygon points="130,202 126,224 134,224" fill={surfFill(rud)} stroke="#0a1420" />
        </g>

        {/* Prop / motor at the nose */}
        <circle cx="130" cy="20" r="13" fill={ACTIVE} opacity={0.05 + throttle * 0.35} />
        <g style={spin ? { animation: `propspin ${spin}s linear infinite`,
                           transformOrigin: '130px 20px' } : { transformOrigin: '130px 20px' }}>
          <rect x="128.5" y="8" width="3" height="24" rx="1.5" fill={ACTIVE} opacity="0.9" />
          <rect x="118" y="18.5" width="24" height="3" rx="1.5" fill={ACTIVE} opacity="0.9" />
        </g>
      </svg>

      {!have && (
        <div className="controls-warn">No servo output yet — connect a vehicle.</div>
      )}

      {/* Throttle bar */}
      <div className="thr-row">
        <span className="io-label">THR</span>
        <div className="thr-track"><div className="thr-fill"
          style={{ width: `${throttle * 100}%` }} /></div>
        <span className="thr-val">{Math.round(throttle * 100)}%</span>
      </div>

      {/* Input -> Output for the 4 primary controls */}
      <div className="io-head"><span>SURFACE</span><span>IN</span><span /><span>OUT</span></div>
      <IOEntry label="Aileron" inv={rc[0]} outv={out[0]} />
      <IOEntry label="Elevator" inv={rc[1]} outv={out[1]} />
      <IOEntry label="Throttle" inv={rc[2]} outv={out[2]} />
      <IOEntry label="Rudder" inv={rc[3]} outv={out[3]} />

      <div className="controls-hint">
        Move each stick — the matching surface should deflect and the IN→OUT
        values should track. Throttle spins the prop.
      </div>
    </div>
  );
}

export default ControlsPanel;

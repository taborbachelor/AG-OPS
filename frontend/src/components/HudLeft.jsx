import React from 'react';

function CircularGauge({ value, max, label, unit, size = 110 }) {
  const radius = (size - 10) / 2;
  const circumference = 2 * Math.PI * radius;
  const pct = Math.min(Math.abs(value) / max, 1);
  const offset = circumference - pct * circumference;

  return (
    <div className="gauge" style={{ width: size, height: size }}>
      <svg width={size} height={size}>
        <circle className="gauge-track" cx={size / 2} cy={size / 2} r={radius} />
        <circle
          className="gauge-fill"
          cx={size / 2} cy={size / 2} r={radius}
          strokeDasharray={circumference}
          strokeDashoffset={offset}
        />
      </svg>
      <div className="gauge-inner">
        <div className="gauge-value">{typeof value === 'number' ? value.toFixed(1) : value}</div>
        <div className="gauge-unit">{unit}</div>
        <div className="gauge-label">{label}</div>
      </div>
    </div>
  );
}

function AttitudeIndicator({ pitch, roll }) {
  const pitchDeg = pitch * 57.2958;
  const rollDeg = roll * 57.2958;
  const pitchOffset = Math.max(-40, Math.min(40, pitchDeg * 1.5));

  return (
    <div className="attitude-indicator">
      <div
        className="ai-horizon"
        style={{
          transform: `rotate(${-rollDeg}deg) translateY(${pitchOffset}px)`,
        }}
      >
        <div className="ai-sky" />
        <div className="ai-ground" />
        <div className="ai-horizon-line" />
        <div className="ai-pitch-lines">
          <div className="ai-pitch-line" />
          <div className="ai-pitch-line" />
          <div className="ai-pitch-line" style={{ width: '20%' }} />
          <div className="ai-pitch-line" style={{ width: '20%' }} />
          <div className="ai-pitch-line" />
          <div className="ai-pitch-line" />
        </div>
      </div>
      <div className="ai-center-mark" />
      <div className="ai-ring" />
    </div>
  );
}

function HudLeft({ telemetry }) {
  const t = telemetry;

  return (
    <div className="hud-left">
      <AttitudeIndicator pitch={t.pitch} roll={t.roll} />
      <CircularGauge value={t.altitude} max={500} label="Alt" unit="m" />
      <CircularGauge value={t.airspeed} max={50} label="Air" unit="m/s" />
    </div>
  );
}

export default HudLeft;

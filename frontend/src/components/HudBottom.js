import React from 'react';

function HudBottom({ telemetry }) {
  const t = telemetry;

  return (
    <div className="hud-bottom">
      <div className="telemetry-strip">
        <div className="telemetry-item">
          <div className="telemetry-item-value">{t.groundspeed.toFixed(1)}</div>
          <div className="telemetry-item-label">GND SPD m/s</div>
        </div>
        <div className="telemetry-item">
          <div className="telemetry-item-value">{t.airspeed.toFixed(1)}</div>
          <div className="telemetry-item-label">AIR SPD m/s</div>
        </div>
        <div className="telemetry-item">
          <div className="telemetry-item-value">{t.altitude.toFixed(1)}</div>
          <div className="telemetry-item-label">ALT m</div>
        </div>
        <div className="telemetry-item">
          <div className="telemetry-item-value">{(t.pitch * 57.2958).toFixed(1)}°</div>
          <div className="telemetry-item-label">PITCH</div>
        </div>
        <div className="telemetry-item">
          <div className="telemetry-item-value">{(t.roll * 57.2958).toFixed(1)}°</div>
          <div className="telemetry-item-label">ROLL</div>
        </div>
      </div>

      <div className="telemetry-strip">
        <div className="telemetry-item">
          <div className="telemetry-item-value">{t.battery_voltage.toFixed(1)}V</div>
          <div className="telemetry-item-label">BATT</div>
        </div>
        <div className="telemetry-item">
          <div className="telemetry-item-value">{t.battery_current.toFixed(1)}A</div>
          <div className="telemetry-item-label">CURRENT</div>
        </div>
        <div className="telemetry-item">
          <div className="telemetry-item-value">{t.gps_satellites}</div>
          <div className="telemetry-item-label">SATS</div>
        </div>
      </div>
    </div>
  );
}

export default HudBottom;

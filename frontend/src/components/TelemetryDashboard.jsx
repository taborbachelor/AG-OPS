import React from 'react';

function TelemetryGauge({ label, value, unit, warn }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      padding: '4px 0', borderBottom: '1px solid #1e293b',
    }}>
      <span style={{ fontSize: 12, color: '#94a3b8' }}>{label}</span>
      <span style={{ fontSize: 14, fontWeight: 600, color: warn ? '#f87171' : '#e0e0e0', fontFamily: 'monospace' }}>
        {value} <span style={{ fontSize: 10, color: '#64748b' }}>{unit}</span>
      </span>
    </div>
  );
}

function TelemetryDashboard({ telemetry }) {
  const t = telemetry;
  const batteryWarn = t.battery_level !== null && t.battery_level < 20;
  const gpsText = ['No GPS', 'No Fix', '2D Fix', '3D Fix'][t.gps_fix] || `Fix: ${t.gps_fix}`;

  return (
    <div className="panel" style={{ flex: 1 }}>
      <div className="panel-title">Telemetry</div>

      <div style={{ marginBottom: 8 }}>
        <div style={{ fontSize: 11, color: '#64748b', marginBottom: 4 }}>POSITION</div>
        <TelemetryGauge label="Altitude" value={t.altitude.toFixed(1)} unit="m" />
        <TelemetryGauge label="Heading" value={t.heading} unit="°" />
        <TelemetryGauge label="Lat" value={t.lat.toFixed(6)} unit="" />
        <TelemetryGauge label="Lon" value={t.lon.toFixed(6)} unit="" />
      </div>

      <div style={{ marginBottom: 8 }}>
        <div style={{ fontSize: 11, color: '#64748b', marginBottom: 4 }}>SPEED</div>
        <TelemetryGauge label="Airspeed" value={t.airspeed.toFixed(1)} unit="m/s" />
        <TelemetryGauge label="Ground Speed" value={t.groundspeed.toFixed(1)} unit="m/s" />
      </div>

      <div style={{ marginBottom: 8 }}>
        <div style={{ fontSize: 11, color: '#64748b', marginBottom: 4 }}>ATTITUDE</div>
        <TelemetryGauge label="Pitch" value={(t.pitch * 57.2958).toFixed(1)} unit="°" />
        <TelemetryGauge label="Roll" value={(t.roll * 57.2958).toFixed(1)} unit="°" />
        <TelemetryGauge label="Yaw" value={(t.yaw * 57.2958).toFixed(1)} unit="°" />
      </div>

      <div style={{ marginBottom: 8 }}>
        <div style={{ fontSize: 11, color: '#64748b', marginBottom: 4 }}>BATTERY</div>
        <TelemetryGauge label="Voltage" value={t.battery_voltage.toFixed(1)} unit="V" warn={batteryWarn} />
        <TelemetryGauge label="Current" value={t.battery_current.toFixed(1)} unit="A" />
        <TelemetryGauge label="Level" value={t.battery_level ?? '--'} unit="%" warn={batteryWarn} />
      </div>

      <div>
        <div style={{ fontSize: 11, color: '#64748b', marginBottom: 4 }}>GPS</div>
        <TelemetryGauge label="Fix" value={gpsText} unit="" warn={t.gps_fix < 3} />
        <TelemetryGauge label="Satellites" value={t.gps_satellites} unit="" warn={t.gps_satellites < 6} />
      </div>
    </div>
  );
}

export default TelemetryDashboard;

import React, { useState, useEffect, useRef, useCallback } from 'react';

import { API } from '../api';
const SPEEDS = [1, 2, 4, 10];

const toTelem = (s) => ({
  connected: true, armed: true, mode: s.mode || 'PLAYBACK',
  altitude: s.alt, airspeed: s.as, groundspeed: s.gs, heading: s.hdg,
  lat: s.lat, lon: s.lon, battery_voltage: s.volt, battery_current: 0,
  battery_level: s.batt, pitch: s.pitch, roll: s.roll, yaw: s.yaw,
  gps_fix: 6, gps_satellites: 10,
});

const fmtDur = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

function LogsPanel({ setPlaybackTelem, setPlaybackPath }) {
  const [logs, setLogs] = useState([]);
  const [selected, setSelected] = useState(null); // name
  const [samples, setSamples] = useState([]);
  const [playing, setPlaying] = useState(false);
  const [head, setHead] = useState(0); // seconds
  const [speed, setSpeed] = useState(2);
  const [status, setStatus] = useState('');
  const timerRef = useRef(null);

  const duration = samples.length ? samples[samples.length - 1].t : 0;

  const refresh = useCallback(async () => {
    try {
      const r = await fetch(`${API}/logs`);
      const d = await r.json();
      setLogs(d.logs || []);
    } catch { setStatus('Could not load logs'); }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  // Clear playback override when the panel unmounts (back to live view).
  useEffect(() => () => { setPlaybackTelem(null); setPlaybackPath(null); },
    [setPlaybackTelem, setPlaybackPath]);

  const sampleAt = useCallback((t) => {
    // last sample with sample.t <= t
    let lo = 0, hi = samples.length - 1, res = 0;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (samples[mid].t <= t) { res = mid; lo = mid + 1; } else { hi = mid - 1; }
    }
    return samples[res];
  }, [samples]);

  const load = async (name) => {
    setPlaying(false);
    try {
      const r = await fetch(`${API}/logs/${name}`);
      const d = await r.json();
      const s = d.samples || [];
      setSelected(name);
      setSamples(s);
      setHead(0);
      setPlaybackPath(s.filter((x) => x.lat).map((x) => [x.lat, x.lon]));
      if (s.length) setPlaybackTelem(toTelem(s[0]));
      setStatus(`Loaded ${s.length} samples`);
    } catch { setStatus('Load failed'); }
  };

  // Playback timer
  useEffect(() => {
    if (!playing || !samples.length) return;
    const TICK = 0.1;
    timerRef.current = setInterval(() => {
      setHead((h) => {
        const next = h + TICK * speed;
        if (next >= duration) {
          setPlaying(false);
          setPlaybackTelem(toTelem(samples[samples.length - 1]));
          return duration;
        }
        setPlaybackTelem(toTelem(sampleAt(next)));
        return next;
      });
    }, TICK * 1000);
    return () => clearInterval(timerRef.current);
  }, [playing, samples, speed, duration, sampleAt, setPlaybackTelem]);

  const scrub = (t) => {
    setHead(t);
    if (samples.length) setPlaybackTelem(toTelem(sampleAt(t)));
  };

  return (
    <div className="logs-panel glass-panel">
      <div className="panel-title" style={{ marginBottom: 10 }}>Flight Logs</div>

      {!selected ? (
        <div className="logs-list">
          {logs.length === 0 && (
            <div className="logs-empty">No flights recorded yet.<br />
              A log is saved automatically each time the aircraft arms.</div>
          )}
          {logs.map((l) => (
            <div key={l.name} className="log-row" onClick={() => load(l.name)}>
              <div className="log-when">{(l.started || l.name).replace('T', ' ')}</div>
              <div className="log-meta">{fmtDur(l.duration)} · {l.samples} pts · {l.size_kb} KB</div>
            </div>
          ))}
        </div>
      ) : (
        <div className="playback">
          <button className="control-btn" onClick={() => {
            setSelected(null); setSamples([]); setPlaying(false);
            setPlaybackTelem(null); setPlaybackPath(null);
          }} style={{ fontSize: 11, alignSelf: 'flex-start', marginBottom: 8 }}>
            ← Back to list
          </button>

          <div className="log-name">{selected}</div>

          <input type="range" min={0} max={duration} step={0.1} value={head}
            onChange={(e) => scrub(Number(e.target.value))} className="scrubber" />
          <div className="playback-time">
            <span>{fmtDur(head)}</span><span>{fmtDur(duration)}</span>
          </div>

          <div className="playback-controls">
            <button className="control-btn success" onClick={() => setPlaying((p) => !p)}
              style={{ flex: 1 }}>
              {playing ? '❚❚ Pause' : '▶ Play'}
            </button>
            <button className="control-btn" onClick={() => { scrub(0); setPlaying(false); }}>
              ⤒ Reset
            </button>
          </div>

          <div className="speed-row">
            <span style={{ fontSize: 11, color: 'var(--text-dim)' }}>Speed</span>
            {SPEEDS.map((s) => (
              <button key={s} className={`map-ctrl-btn ${speed === s ? 'active' : ''}`}
                onClick={() => setSpeed(s)}>{s}×</button>
            ))}
          </div>
        </div>
      )}

      {status && <div className="safety-status">{status}</div>}
    </div>
  );
}

export default LogsPanel;

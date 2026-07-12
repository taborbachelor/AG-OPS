import React, { useState } from 'react';

const API = 'http://localhost:8000/api';

// m² per acre — matches the backend constant so the live readout agrees
// with the server-priced acreage once an order is loaded.
const SQ_M_PER_ACRE = 4046.8564224;
const EARTH_R = 6371000;
const DEG = Math.PI / 180;

// Planar shoelace on an equirectangular projection (x scaled by cos(lat)).
// Sub-1% error at field scale — plenty for a live readout while drawing.
function fieldAcres(pts) {
  if (pts.length < 3) return 0;
  const lat0 = (pts.reduce((s, p) => s + p.lat, 0) / pts.length) * DEG;
  const kx = Math.cos(lat0) * EARTH_R * DEG; // deg lon -> m
  const ky = EARTH_R * DEG;                  // deg lat -> m
  let area = 0;
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i];
    const b = pts[(i + 1) % pts.length];
    area += a.lon * kx * (b.lat * ky) - b.lon * kx * (a.lat * ky);
  }
  return Math.abs(area / 2) / SQ_M_PER_ACRE;
}

function centroidOf(pts) {
  return {
    lat: pts.reduce((s, p) => s + p.lat, 0) / pts.length,
    lon: pts.reduce((s, p) => s + p.lon, 0) / pts.length,
  };
}

// Zone-lookup radius: farthest vertex from the centroid plus margin for the
// largest keepout buffer, clamped to the backend's 5 km Overpass cap.
function lookupRadius(pts, c) {
  let max = 0;
  for (const p of pts) {
    const dx = (p.lon - c.lon) * DEG * EARTH_R * Math.cos(c.lat * DEG);
    const dy = (p.lat - c.lat) * DEG * EARTH_R;
    const d = Math.hypot(dx, dy);
    if (d > max) max = d;
  }
  return Math.round(Math.min(5000, Math.max(400, max + 250)));
}

const fmt = (v, digits) => (Number.isFinite(v) ? v.toFixed(digits) : '—');

function NumField({ label, value, unit, onChange }) {
  return (
    <div className="safety-field">
      <span className="safety-label">{label}</span>
      <input type="number" value={value}
        onChange={(e) => onChange(Number(e.target.value))} style={{ width: 70 }} />
      <span className="safety-unit">{unit}</span>
    </div>
  );
}

function Stat({ v, l }) {
  return (
    <div className="spray-stat">
      <span className="spray-stat-val">{v}</span>
      <span className="spray-stat-label">{l}</span>
    </div>
  );
}

function SprayPanel({
  connected, field, setField, drawing, setDrawing,
  plan, setPlan, zones, setZones,
}) {
  const [orderId, setOrderId] = useState('');
  const [order, setOrder] = useState(null);
  const [swath, setSwath] = useState(40);
  const [alt, setAlt] = useState(100);
  const [bufWater, setBufWater] = useState(15);
  const [bufTrees, setBufTrees] = useState(10);
  const [bufBuildings, setBufBuildings] = useState(10);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');          // transient errors
  const [keepouts, setKeepouts] = useState(null);    // count from plan_auto stats
  const [zonesNote, setZonesNote] = useState('');    // amber caveat on the plan card
  const [upStatus, setUpStatus] = useState(null);    // {ok, msg} after upload

  const acres = fieldAcres(field);
  const flash = (m) => { setStatus(m); setTimeout(() => setStatus(''), 5000); };

  const resetResults = () => {
    setPlan(null); setZones(null); setKeepouts(null);
    setZonesNote(''); setUpStatus(null);
  };

  const clearField = () => {
    setField([]); setOrder(null); setDrawing(false);
    resetResults();
  };

  const loadOrder = async () => {
    const id = orderId.trim();
    if (!id) return;
    setBusy(true);
    try {
      const res = await fetch(`${API}/orders/${encodeURIComponent(id)}`);
      if (!res.ok) {
        flash(res.status === 404 ? 'Order not found (or orders API not live)'
          : `Order load failed (${res.status})`);
        setBusy(false);
        return;
      }
      const o = await res.json();
      // field_geojson is a STRING of a GeoJSON Polygon whose ring is
      // [lon,lat] pairs with the first point repeated — swap and unclose.
      const gj = JSON.parse(o.field_geojson);
      let pts = ((gj.coordinates && gj.coordinates[0]) || [])
        .map(([lon, lat]) => ({ lat, lon }));
      const first = pts[0];
      const last = pts[pts.length - 1];
      if (pts.length > 1 && first.lat === last.lat && first.lon === last.lon) {
        pts = pts.slice(0, -1);
      }
      if (pts.length < 3) {
        flash('Order has no usable field polygon');
      } else {
        setField(pts);
        setDrawing(false);
        resetResults();
        setOrder({ name: o.name, acres: o.acres, date: o.date, slot: o.slot, status: o.status });
      }
    } catch (e) {
      flash('Order load error — is the backend running?');
    }
    setBusy(false);
  };

  // Overlay zones for the map. plan_auto may return them inline one day;
  // until then (and on the fallback path) the live /api/zones endpoint is
  // the source of truth.
  const fetchZones = async () => {
    try {
      const c = centroidOf(field);
      const r = lookupRadius(field, c);
      const res = await fetch(`${API}/zones/?lat=${c.lat}&lon=${c.lon}&radius=${r}`);
      if (!res.ok) throw new Error(String(res.status));
      const z = await res.json();
      setZones({ water: z.water || [], trees: z.trees || [], buildings: z.buildings || [] });
    } catch (e) {
      setZones(null);
      setZonesNote((n) => n || 'Zones unavailable — no overlay to show');
    }
  };

  const generate = async () => {
    if (field.length < 3) { flash('Draw or load a field first (3+ points)'); return; }
    setBusy(true);
    resetResults();
    try {
      const headers = { 'Content-Type': 'application/json' };
      // Preferred path: the auto-keepout planner. 404/405 means that
      // endpoint isn't deployed yet — fall back to the plain planner.
      let usedAuto = true;
      let res = await fetch(`${API}/coverage/plan_auto`, {
        method: 'POST', headers,
        body: JSON.stringify({
          polygon: field, swath, alt,
          water_buffer: bufWater, tree_buffer: bufTrees, building_buffer: bufBuildings,
        }),
      });
      if (res.status === 404 || res.status === 405) {
        usedAuto = false;
        res = await fetch(`${API}/coverage/plan`, {
          method: 'POST', headers,
          body: JSON.stringify({ polygon: field, swath, alt }),
        });
      }
      const data = await res.json();
      if (!res.ok) {
        flash(`Plan failed: ${data.detail || res.status}`);
        setBusy(false);
        return;
      }
      setPlan(data);
      if (usedAuto) {
        const s = data.stats || {};
        const k = [s.keepouts_applied, s.n_keepouts, s.keepouts]
          .find((v) => typeof v === 'number');
        setKeepouts(k === undefined ? null : k);
        // plan_auto degrades to an UNCLIPPED plan when Overpass is down
        // (200 + zones_unavailable:true) — warn exactly like the fallback
        // path so the operator never mistakes it for a keepout-aware plan.
        if (data.zones_unavailable) {
          setZonesNote('Zones unavailable — path does not avoid no-spray areas');
        }
      } else {
        setZonesNote('Zones unavailable — path does not avoid no-spray areas');
      }
      if (usedAuto && data.zones && data.zones.water) {
        setZones(data.zones);
      } else {
        await fetchZones();
      }
    } catch (e) {
      flash('Plan error — is the backend running?');
    }
    setBusy(false);
  };

  const upload = async () => {
    const wps = (plan && plan.waypoints) || [];
    if (wps.length === 0) { setUpStatus({ ok: false, msg: 'Generate a plan first' }); return; }
    setBusy(true);
    try {
      const first = wps[0];
      // TAKEOFF toward the first pass, fly every plan waypoint, then RTL
      // (home is auto-inserted at seq 0 by the backend).
      const items = [
        { command: 'TAKEOFF', lat: first.lat, lon: first.lon, alt: 80, param1: 0 },
        ...wps.map((w) => ({
          command: 'WAYPOINT', lat: w.lat, lon: w.lon,
          alt: Number(w.alt != null ? w.alt : alt), param1: 0,
        })),
        { command: 'RTL', lat: first.lat, lon: first.lon, alt: 80, param1: 0 },
      ];
      const res = await fetch(`${API}/mission/upload`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items }),
      });
      const data = await res.json();
      setUpStatus(res.ok
        ? { ok: true, msg: `Mission uploaded — ${data.count != null ? data.count : items.length} items ✓` }
        : { ok: false, msg: `Upload failed: ${data.detail || res.status}` });
    } catch (e) {
      setUpStatus({ ok: false, msg: 'Upload error — is the backend running?' });
    }
    setBusy(false);
  };

  const stats = plan && plan.stats;

  return (
    <div className="spray-panel glass-panel">
      <div className="panel-title" style={{ marginBottom: 10 }}>Spray Job</div>

      <div className="spray-scroll">
        {/* FIELD: hand-drawn boundary or a customer order's polygon */}
        <div className="safety-card">
          <div className="safety-card-head">
            <span>FIELD</span>
            <span className="spray-acres">
              {acres > 0 ? `${acres.toFixed(2)} ac` : `${field.length} pts`}
            </span>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button
              className={`control-btn ${drawing ? 'active' : ''}`}
              onClick={() => setDrawing(!drawing)}
              style={{ flex: 1 }}
            >
              {drawing ? 'Drawing…' : 'Draw field'}
            </button>
            <button className="control-btn" onClick={() => setDrawing(false)} disabled={!drawing}>
              Close
            </button>
            <button className="control-btn danger" onClick={clearField}
              disabled={field.length === 0 && !order}>
              Clear
            </button>
          </div>
          {drawing && (
            <div className="spray-hint">Click the map to add vertices</div>
          )}
          <div className="safety-field" style={{ marginTop: 4 }}>
            <input
              placeholder="Order ID"
              value={orderId}
              onChange={(e) => setOrderId(e.target.value)}
              style={{ flex: 1, minWidth: 0 }}
            />
            <button className="control-btn" onClick={loadOrder}
              disabled={busy || !orderId.trim()}>
              Load
            </button>
          </div>
          {order && (
            <div className="spray-order">
              <div className="spray-order-top">
                <span className="spray-order-name">{order.name}</span>
                <span className={`spray-chip ${order.status === 'pending_payment' ? 'warn' : 'ok'}`}>
                  {order.status}
                </span>
              </div>
              <div className="spray-order-meta">
                {order.acres} ac · {order.date} {order.slot}
              </div>
            </div>
          )}
        </div>

        {/* SETTINGS: swath/altitude + no-spray keepout buffers */}
        <div className="safety-card">
          <div className="safety-card-head"><span>SETTINGS</span></div>
          <NumField label="Swath" value={swath} unit="m" onChange={setSwath} />
          <NumField label="Altitude" value={alt} unit="m" onChange={setAlt} />
          <NumField label="Water buf" value={bufWater} unit="m" onChange={setBufWater} />
          <NumField label="Tree buf" value={bufTrees} unit="m" onChange={setBufTrees} />
          <NumField label="Bldg buf" value={bufBuildings} unit="m" onChange={setBufBuildings} />
        </div>

        {/* GENERATE */}
        <button className="control-btn success" onClick={generate}
          disabled={busy || field.length < 3} style={{ width: '100%' }}>
          {busy ? 'Working…' : 'Generate Spray Plan'}
        </button>

        {stats && (
          <div className="safety-card">
            <div className="safety-card-head"><span>PLAN</span></div>
            <div className="spray-stats">
              <Stat v={fmt(stats.area_acres, 1)} l="acres" />
              <Stat v={fmt(stats.n_passes, 0)} l="passes" />
              <Stat v={fmt(stats.path_length_m / 1000, 1)} l="path km" />
              <Stat v={fmt(stats.est_time_s / 60, 0)} l="est min" />
            </div>
            {keepouts != null && (
              <div className="spray-keep">
                {keepouts} keepout{keepouts === 1 ? '' : 's'} applied
              </div>
            )}
            {zones && (
              <div className="spray-zones-line">
                zones: {zones.water.length} water · {zones.trees.length} trees · {zones.buildings.length} bldgs
              </div>
            )}
            {zonesNote && <div className="spray-note">{zonesNote}</div>}
          </div>
        )}

        {/* MISSION */}
        <div className="safety-card">
          <div className="safety-card-head"><span>MISSION</span></div>
          <button className="control-btn success" onClick={upload}
            disabled={!connected || busy || !plan || !(plan.waypoints || []).length}
            style={{ width: '100%' }}>
            Upload Mission
          </button>
          {upStatus && (
            <div className={`spray-status ${upStatus.ok ? 'ok' : 'err'}`}>{upStatus.msg}</div>
          )}
          <div className="spray-hint">
            Arming &amp; launch stay in Launch Control on the FLY view.
          </div>
        </div>
      </div>

      {status && <div className="safety-status">{status}</div>}
      {!connected && (
        <div className="safety-warn">Connect to a vehicle to upload the mission</div>
      )}
    </div>
  );
}

export default SprayPanel;

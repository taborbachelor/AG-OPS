import React, { useRef, useState } from 'react';

import { API } from '../api';

const SQ_M_PER_ACRE = 4046.8564224;
const EARTH_R = 6371000;
const DEG = Math.PI / 180;

// Planar shoelace on an equirectangular projection (x scaled by cos(lat)).
function polyAcres(pts) {
  if (!pts || pts.length < 3) return 0;
  const lat0 = (pts.reduce((s, p) => s + p.lat, 0) / pts.length) * DEG;
  const kx = Math.cos(lat0) * EARTH_R * DEG;
  const ky = EARTH_R * DEG;
  let area = 0;
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i];
    const b = pts[(i + 1) % pts.length];
    area += a.lon * kx * (b.lat * ky) - b.lon * kx * (a.lat * ky);
  }
  return Math.abs(area / 2) / SQ_M_PER_ACRE;
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

// Multi-field spray JOB panel: build a list of fields (draw / snap / auto-
// detect inside a selected area / load from a customer order), then plan the
// whole job at once — per-field spray patterns plus the transit legs between
// them — and upload it as one mission.
function SprayPanel({
  connected, draft, setDraft, fields, setFields, area, setArea,
  drawing, setDrawing, areaDrawing, setAreaDrawing,
  snapping, setSnapping, snapStatus,
  plan, setPlan, zones, setZones, homePos,
}) {
  const [orderId, setOrderId] = useState('');
  const [order, setOrder] = useState(null);
  const [swath, setSwath] = useState(40);
  const [alt, setAlt] = useState(100);
  const [bufWater, setBufWater] = useState(15);
  const [bufTrees, setBufTrees] = useState(10);
  const [bufBuildings, setBufBuildings] = useState(10);
  // Lateral FLIGHT clearance, not a spray-drift margin — hence the wider
  // default. Backend mirrors this (routers/coverage*.py powerline_buffer).
  const [bufPowerline, setBufPowerline] = useState(20);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');
  const [zonesNote, setZonesNote] = useState('');
  const [upStatus, setUpStatus] = useState(null);
  // Backend refused to plan because the zone service is down (fail-closed).
  // Set -> we show a "Plan anyway (no zones)" button that retries with the
  // explicit allow_missing_zones override.
  const [zonesBlocked, setZonesBlocked] = useState(false);
  const [hazardOverflights, setHazardOverflights] = useState(0);
  const [hazardBlocked, setHazardBlocked] = useState(false);
  const [homeLegHazard, setHomeLegHazard] = useState(false);

  // Bumped on every job mutation (field added/removed/cleared). An in-flight
  // plan request captures the value at launch and discards its response if
  // the job changed underneath it — otherwise a stale plan for fields that no
  // longer exist would render and be uploadable.
  const planReq = useRef(0);

  const flash = (m) => { setStatus(m); setTimeout(() => setStatus(''), 5000); };

  const resetResults = () => {
    planReq.current += 1;
    setPlan(null); setZones(null); setZonesNote(''); setUpStatus(null);
    setZonesBlocked(false);
    setHazardOverflights(0); setHomeLegHazard(false); setHazardBlocked(false);
  };

  // Exclusive input modes: draw / area / snap.
  const setMode = (mode) => {
    setDrawing(mode === 'draw' ? !drawing : false);
    setAreaDrawing(mode === 'area' ? !areaDrawing : false);
    setSnapping(mode === 'snap' ? !snapping : false);
  };

  const commitDraft = () => {
    if (draft.length < 3) { flash('Need at least 3 points'); return; }
    setFields((f) => [...f, { polygon: draft, acres: null, source: 'drawn' }]);
    setDraft([]);
    setDrawing(false);
    resetResults();
  };

  const removeField = (i) => {
    setFields((f) => f.filter((_, j) => j !== i));
    resetResults();
  };

  const clearJob = () => {
    setFields([]); setDraft([]); setArea([]); setOrder(null);
    setDrawing(false); setAreaDrawing(false); setSnapping(false);
    resetResults();
  };

  // Auto-detect mapped parcels inside the selected area.
  const detectFields = async () => {
    if (area.length < 3) { flash('Close the selection area first'); return; }
    setBusy(true);
    try {
      const res = await fetch(`${API}/fields/detect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ polygon: area }),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || res.status);
      if (d.found === 0) {
        flash('No mapped fields in that area — add them with Draw or Snap');
      } else {
        setFields((f) => [
          ...f,
          ...d.fields.map((fd) => ({
            polygon: fd.polygon,
            holes: fd.holes || [],   // in-field non-crop islands -> keepouts
            acres: fd.acres,
            // Field list rows read "356.7 ac · Corn" when USDA supplied a crop.
            source: (fd.tags && fd.tags.crop) || 'auto',
          })),
        ]);
        resetResults();
        flash(`${d.found} field${d.found === 1 ? '' : 's'} detected ✓`);
      }
    } catch (e) {
      flash(`Detect failed: ${e.message}`);
    }
    setBusy(false);
  };

  const loadOrder = async () => {
    const id = orderId.trim();
    if (!id) return;
    setBusy(true);
    try {
      const res = await fetch(`${API}/orders/${encodeURIComponent(id)}`);
      if (!res.ok) {
        flash(res.status === 404 ? 'Order not found' : `Order load failed (${res.status})`);
        setBusy(false);
        return;
      }
      const o = await res.json();
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
        setFields((f) => [...f, { polygon: pts, acres: o.acres, source: `order` }]);
        resetResults();
        setOrder({ name: o.name, acres: o.acres, date: o.date, slot: o.slot, status: o.status });
        flash(`Order field added to job ✓`);
      }
    } catch (e) {
      flash('Order load error — is the backend running?');
    }
    setBusy(false);
  };

  // One call plans the whole job: per-field zone-aware coverage + transit
  // ordering. Zones come back inline. Zone-service failure is FAIL-CLOSED:
  // the backend refuses unless allowMissingZones explicitly overrides.
  const generate = async (allowMissingZones = false,
    allowHazardCrossings = false) => {
    let jobFields = fields;
    if (jobFields.length === 0 && draft.length >= 3) {
      // Courtesy: an unclosed draft becomes the job's single field.
      jobFields = [{ polygon: draft, acres: null, source: 'drawn' }];
      setFields(jobFields);
      setDraft([]);
      setDrawing(false);
    }
    if (jobFields.length === 0) { flash('Add at least one field first'); return; }
    setBusy(true);
    resetResults();
    setZonesBlocked(false);
    const req = planReq.current; // staleness token for this request
    // Detected in-field holes (farmsteads/ponds/tree stands) ride along as
    // keepouts so the passes clip around them automatically.
    const holeKeepouts = jobFields.flatMap((f) => f.holes || []);
    try {
      const res = await fetch(`${API}/coverage/plan_multi`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          fields: jobFields.map((f) => f.polygon),
          swath, alt,
          water_buffer: bufWater, tree_buffer: bufTrees, building_buffer: bufBuildings,
          powerline_buffer: bufPowerline,
          home: homePos || undefined,
          keepouts: holeKeepouts.length ? holeKeepouts : undefined,
          allow_missing_zones: allowMissingZones || undefined,
          allow_hazard_crossings: allowHazardCrossings || undefined,
        }),
      });
      const data = await res.json();
      if (req !== planReq.current) {
        // Job was edited (field removed / cleared) while planning — this
        // response no longer matches the shown fields. Drop it.
        setBusy(false);
        return;
      }
      if (!res.ok) {
        const detail = data.detail;
        if (detail && detail.error === 'zones_unavailable') {
          // Fail-closed refusal: no plan without zone data unless the
          // operator explicitly accepts flying without keepouts.
          setZonesBlocked(true);
          setZonesNote('Zone service unavailable — no plan generated. '
            + 'Retry, or plan anyway WITHOUT water/tree/building keepouts.');
        } else if (detail && detail.error === 'hazard_crossings') {
          // Fail-closed refusal: the plan contains legs that fly THROUGH a
          // powerline corridor. Measured at 1.7 m from a live 115 kV line
          // before this gate existed — a warning was not enough.
          setHazardBlocked(true);
          setHazardOverflights(detail.count || 0);
          setZonesNote(`${detail.count} leg(s) cross a powerline corridor and `
            + 'could not be routed around — no plan generated.');
        } else {
          flash(`Plan failed: ${(detail && detail.message) || detail || res.status}`);
        }
        setBusy(false);
        return;
      }
      setPlan(data);
      const holesAsZones = holeKeepouts.map((h) => ({ kind: 'hole', coords: h }));
      if (data.zones_unavailable) {
        setZonesNote(holeKeepouts.length
          ? 'Zone service down — only detected in-field holes are avoided'
          : 'Zones unavailable — paths do not avoid no-spray areas');
        setZones(holesAsZones.length
          ? { water: [], trees: [], buildings: [], powerline: [], holes: holesAsZones }
          : null);
      } else if (data.zones && data.zones.water) {
        setZones({
          water: data.zones.water || [],
          trees: data.zones.trees || [],
          buildings: data.zones.buildings || [],
          powerline: data.zones.powerline || [],
          holes: holesAsZones,
        });
      }
      if ((data.skipped || []).length > 0) {
        flash(`${data.skipped.length} field(s) skipped: ${data.skipped[0].error}`);
      }
      const totals = data.totals || {};
      const overflights = totals.keepout_overflights || 0;
      if (overflights > 0) {
        // Connector legs are NOT rerouted around spray-quality keepouts — the
        // aircraft physically overflies them (mind trees at spray altitude).
        // Hazard keepouts (powerlines) ARE routed around; see below.
        setZonesNote((n) => `${n ? n + ' · ' : ''}${overflights} connecting `
          + 'leg(s) cross no-spray zones — aircraft overflies them '
          + '(sprayer off; mind trees at low altitude)');
      }
      if (totals.hazard_reroutes > 0) {
        setZonesNote((n) => `${n ? n + ' · ' : ''}${totals.hazard_reroutes} `
          + 'leg(s) routed around powerlines');
      }
      // The one that is not a statistic: legs we could NOT route. The plan
      // still crosses a line there, so it has to be impossible to miss.
      setHazardOverflights(totals.hazard_overflights || 0);
      setHomeLegHazard(Boolean(totals.home_leg_hazard));
    } catch (e) {
      if (req === planReq.current) flash('Plan error — is the backend running?');
    }
    setBusy(false);
  };

  const upload = async () => {
    const wps = (plan && plan.combined_waypoints) || [];
    if (wps.length === 0) { setUpStatus({ ok: false, msg: 'Generate a plan first' }); return; }
    setBusy(true);
    try {
      const first = wps[0];
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

  const totals = plan && plan.totals;
  const keepoutsApplied = plan
    ? plan.fields.reduce((s, f) => s + (f.stats.keepouts_applied || 0), 0)
    : 0;
  const jobAcres = fields.reduce((s, f) => s + (f.acres != null ? f.acres : polyAcres(f.polygon)), 0);

  return (
    <div className="spray-panel glass-panel">
      <div className="panel-title" style={{ marginBottom: 10 }}>Spray Job</div>

      <div className="spray-scroll">
        {/* FIELDS: build the job's field list */}
        <div className="safety-card">
          <div className="safety-card-head">
            <span>FIELDS ({fields.length})</span>
            <span className="spray-acres">
              {jobAcres > 0 ? `${jobAcres.toFixed(1)} ac` : ''}
            </span>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button className={`control-btn ${drawing ? 'active' : ''}`}
              onClick={() => setMode('draw')} style={{ flex: 1 }}>
              {drawing ? 'Drawing…' : 'Draw'}
            </button>
            <button className={`control-btn ${areaDrawing ? 'active' : ''}`}
              onClick={() => setMode('area')} style={{ flex: 1 }}
              title="Select an area, then auto-detect the fields inside it">
              {areaDrawing ? 'Area…' : 'Area'}
            </button>
            <button className={`control-btn ${snapping ? 'active' : ''}`}
              onClick={() => setMode('snap')} style={{ flex: 1 }}
              title="Click a field to snap to its mapped boundary">
              {snapping ? 'Click…' : 'Snap'}
            </button>
          </div>

          {drawing && (
            <>
              <div className="spray-hint">
                Click the map to outline a field ({draft.length} pts
                {draft.length >= 3 ? ` · ${polyAcres(draft).toFixed(1)} ac` : ''})
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
                <button className="control-btn success" onClick={commitDraft}
                  disabled={draft.length < 3} style={{ flex: 1 }}>
                  Add field to job
                </button>
                <button className="control-btn" onClick={() => setDraft([])}
                  disabled={draft.length === 0}>
                  Restart
                </button>
              </div>
            </>
          )}

          {areaDrawing && (
            <div className="spray-hint">
              Click the map to outline the search area ({area.length} pts)
            </div>
          )}
          {area.length >= 3 && (
            <div style={{ display: 'flex', gap: 6 }}>
              <button className="control-btn success" onClick={() => { setAreaDrawing(false); detectFields(); }}
                disabled={busy} style={{ flex: 1 }}>
                {busy ? 'Detecting…' : 'Detect fields in area'}
              </button>
              <button className="control-btn" onClick={() => { setArea([]); setAreaDrawing(false); }}>
                ×
              </button>
            </div>
          )}

          {snapping && (
            <div className="spray-hint">Click inside a field — it snaps to the mapped perimeter</div>
          )}
          {snapStatus && <div className="spray-hint">{snapStatus}</div>}

          {fields.length > 0 && (
            <div className="sfield-list">
              {fields.map((f, i) => (
                <div key={i} className="sfield-row">
                  <span className="sfield-n">{i + 1}</span>
                  <span className="sfield-meta">
                    {(f.acres != null ? f.acres : polyAcres(f.polygon)).toFixed(1)} ac · {f.source}
                  </span>
                  <button className="wp-del" onClick={() => removeField(i)}
                    disabled={busy} title="Remove">×</button>
                </div>
              ))}
            </div>
          )}

          <div className="safety-field" style={{ marginTop: 4 }}>
            <input placeholder="Order ID" value={orderId}
              onChange={(e) => setOrderId(e.target.value)} style={{ flex: 1, minWidth: 0 }} />
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

          {(fields.length > 0 || area.length > 0 || draft.length > 0) && (
            <button className="control-btn danger" onClick={clearJob}
              disabled={busy} style={{ width: '100%' }}>
              Clear job
            </button>
          )}
        </div>

        {/* SETTINGS */}
        <div className="safety-card">
          <div className="safety-card-head"><span>SETTINGS</span></div>
          <NumField label="Swath" value={swath} unit="m" onChange={setSwath} />
          <NumField label="Altitude" value={alt} unit="m" onChange={setAlt} />
          <NumField label="Water buf" value={bufWater} unit="m" onChange={setBufWater} />
          <NumField label="Tree buf" value={bufTrees} unit="m" onChange={setBufTrees} />
          <NumField label="Bldg buf" value={bufBuildings} unit="m" onChange={setBufBuildings} />
          <NumField label="Powerline buf" value={bufPowerline} unit="m" onChange={setBufPowerline} />
          <div className="spray-hint" style={{ textAlign: 'left' }}>
            Powerline standoff is lateral flight clearance, not spray drift.
          </div>
        </div>

        <button className="control-btn success" onClick={() => generate()}
          disabled={busy || (fields.length === 0 && draft.length < 3)} style={{ width: '100%' }}>
          {busy ? 'Working…' : `Generate Spray Plan${fields.length > 1 ? ` (${fields.length} fields)` : ''}`}
        </button>

        {hazardBlocked && (
          <div className="safety-card">
            <div className="spray-note" style={{ color: '#ff5c5c', fontWeight: 600 }}>
              {zonesNote}
            </div>
            <div className="spray-hint" style={{ textAlign: 'left' }}>
              A powerline usually runs far past the field, so there is no short
              way around it. Either split the field along the line and spray
              each side as its own job, or accept the crossings and fly them
              manually at a safe crossing altitude.
            </div>
            <button className="control-btn danger" onClick={() => generate(false, true)}
              disabled={busy} style={{ width: '100%' }}>
              Plan anyway — legs WILL cross the powerline
            </button>
          </div>
        )}

        {zonesBlocked && (
          <div className="safety-card">
            <div className="spray-note">{zonesNote}</div>
            <button className="control-btn danger" onClick={() => generate(true)}
              disabled={busy} style={{ width: '100%' }}>
              Plan anyway — NO no-spray zones
            </button>
          </div>
        )}

        {totals && (
          <div className="safety-card">
            <div className="safety-card-head"><span>JOB PLAN</span></div>
            <div className="spray-stats">
              <Stat v={totals.fields} l="fields" />
              <Stat v={fmt(totals.area_acres, 1)} l="acres" />
              <Stat v={fmt(totals.est_time_s / 60, 0)} l="est min" />
              <Stat v={fmt(totals.spray_path_m / 1000, 1)} l="spray km" />
              <Stat v={fmt(totals.transit_m / 1000, 1)} l="transit km" />
              <Stat v={totals.waypoints} l="waypoints" />
              {totals.coverage_pct != null && (
                <Stat v={`${totals.coverage_pct}%`} l="coverage" />
              )}
            </div>
            {keepoutsApplied > 0 && (
              <div className="spray-keep">
                {keepoutsApplied} keepout{keepoutsApplied === 1 ? '' : 's'} applied
              </div>
            )}
            {zones && (
              <div className="spray-zones-line">
                zones: {zones.water.length} water · {zones.trees.length} trees · {zones.buildings.length} bldgs
                {(zones.holes || []).length > 0 ? ` · ${zones.holes.length} in-field holes` : ''}
              </div>
            )}
            {(plan.skipped || []).length > 0 && (
              <div className="spray-note">
                {plan.skipped.length} field(s) skipped — {plan.skipped[0].error}
              </div>
            )}
            {zonesNote && <div className="spray-note">{zonesNote}</div>}
            {totals.uncovered_acres > 0.05 && (
              <div className="spray-note">
                {totals.uncovered_acres} acre(s) of sprayable ground not
                covered by any pass — usually the strip alongside a keepout.
                Tighten the swath or re-angle the passes to close it.
              </div>
            )}
            {hazardOverflights > 0 && (
              <div className="spray-note" style={{ color: '#ff5c5c', fontWeight: 600 }}>
                {hazardOverflights} leg(s) STILL cross a powerline corridor —
                could not be routed around. Do not fly this plan without
                checking those legs.
              </div>
            )}
            {homeLegHazard && (
              <div className="spray-note" style={{ color: '#ffd600' }}>
                The return-home path crosses a powerline corridor. RTL flies
                straight home and will NOT avoid it — fly the return manually
                or move the home point.
              </div>
            )}
            <div className="spray-hint" style={{ textAlign: 'left' }}>
              Legend: <span style={{ color: '#00e5ff' }}>■ spray</span> ·{' '}
              <span style={{ color: '#ff9100' }}>■ transit</span> ·{' '}
              <span style={{ color: '#b388ff' }}>■ home legs</span> ·{' '}
              <span style={{ color: '#3b82f6' }}>■ water</span> ·{' '}
              <span style={{ color: '#00e676' }}>■ trees</span> ·{' '}
              <span style={{ color: '#ffd600' }}>■ powerline</span>
            </div>
            {/* ALWAYS on, not only on lookup failure: the dangerous case here
                is "the query succeeded but under-counted". OSM coverage of
                rural distribution lines is inconsistent — this project already
                hit that exact failure mode with parcel boundaries near
                Sabetha. Absence of a mapped line is NOT evidence of no line. */}
            <div className="spray-note" style={{ textAlign: 'left' }}>
              Powerline keepouts are OSM-sourced and may be incomplete —
              confirm the field visually before flight.
            </div>
          </div>
        )}

        {/* MISSION */}
        <div className="safety-card">
          <div className="safety-card-head"><span>MISSION</span></div>
          <button className="control-btn success" onClick={upload}
            disabled={!connected || busy || !plan || !(plan.combined_waypoints || []).length}
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

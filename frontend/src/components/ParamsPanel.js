import React, { useState } from 'react';

const API = 'http://localhost:8000/api';

// Full flight-controller parameter table: download on demand (it's a few
// hundred entries and pauses telemetry for a few seconds), filter, edit
// inline, write back one at a time. Power-user tool — values go straight to
// the autopilot, so the caution note stays visible.
function ParamsPanel({ connected }) {
  const [params, setParams] = useState(null);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState('');
  const [edits, setEdits] = useState({});
  const [note, setNote] = useState('');

  const flash = (m) => { setNote(m); setTimeout(() => setNote(''), 4000); };

  const download = async () => {
    setBusy(true);
    setNote('Downloading parameter table — telemetry pauses briefly…');
    try {
      const r = await fetch(`${API}/vehicle/params`);
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
      setParams(d.params);
      flash(`${d.count} parameters loaded`);
    } catch (e) {
      flash(`Download failed: ${e.message}`);
    }
    setBusy(false);
  };

  const save = async (name) => {
    const v = Number(edits[name]);
    if (!Number.isFinite(v)) { flash('Value must be a number'); return; }
    try {
      const r = await fetch(`${API}/vehicle/params`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, value: v }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setParams((p) => ({ ...p, [name]: v }));
      setEdits((e) => { const c = { ...e }; delete c[name]; return c; });
      flash(`${name} = ${v} sent ✓`);
    } catch {
      flash(`Failed to set ${name}`);
    }
  };

  const names = params
    ? Object.keys(params)
      .filter((n) => n.toLowerCase().includes(filter.toLowerCase()))
      .sort()
    : [];
  const shown = names.slice(0, 200);

  return (
    <div className="params-panel glass-panel">
      <div className="panel-title" style={{ marginBottom: 10 }}>Parameters</div>

      {!params ? (
        <div className="params-empty">
          <p>Download the vehicle's full parameter table to inspect or tune it.</p>
          <button className="control-btn success" onClick={download}
            disabled={!connected || busy} style={{ width: '100%', marginTop: 10 }}>
            {busy ? 'Downloading…' : 'Download parameters'}
          </button>
          {!connected && <div className="safety-warn">Connect to a vehicle first</div>}
        </div>
      ) : (
        <>
          <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
            <input
              placeholder={`Filter ${Object.keys(params).length} params…`}
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              style={{ flex: 1, minWidth: 0 }}
            />
            <button className="control-btn" onClick={download} disabled={busy} title="Re-download">
              ↻
            </button>
          </div>
          <div className="params-list">
            {shown.map((n) => (
              <div key={n} className="param-row">
                <span className="param-name" title={n}>{n}</span>
                <input
                  className="param-value"
                  value={edits[n] != null ? edits[n] : String(params[n])}
                  onChange={(e) => setEdits((ed) => ({ ...ed, [n]: e.target.value }))}
                  onKeyDown={(e) => { if (e.key === 'Enter' && edits[n] != null) save(n); }}
                />
                {edits[n] != null && Number(edits[n]) !== params[n] && (
                  <button className="param-save" onClick={() => save(n)} title="Write to vehicle">
                    SET
                  </button>
                )}
              </div>
            ))}
            {names.length > shown.length && (
              <div className="params-more">…{names.length - shown.length} more — narrow the filter</div>
            )}
            {names.length === 0 && <div className="params-more">No matches</div>}
          </div>
          <div className="params-caution">
            ⚠ Writes go straight to the flight controller. Know the parameter
            before you change it.
          </div>
        </>
      )}

      {note && <div className="safety-status">{note}</div>}
    </div>
  );
}

export default ParamsPanel;

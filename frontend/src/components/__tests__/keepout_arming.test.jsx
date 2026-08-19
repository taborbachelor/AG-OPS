/**
 * Mission upload must ARM the live keepout-proximity monitor.
 *
 * The backend deliberately CLEARS the monitor on mission upload — the
 * aircraft can fly a mission the GCS never planned, and pretending we know
 * its keepouts would be worse than admitting we don't. So the UI has to
 * re-arm it explicitly with the zones the plan was built against.
 *
 * Before this wiring, POST /api/safety/keepouts existed, was unit-tested and
 * was hooked into telemetry and the guardian — and nothing ever called it.
 * The monitor ran with zero rings and could never warn. These tests pin the
 * two halves that matter: that arming happens with the operator's ACTUAL
 * buffer, and that a failure to arm is stated rather than swallowed.
 */
import React from 'react';
import { render, screen, act, fireEvent } from '@testing-library/react';
import { vi } from 'vitest';
import '@testing-library/jest-dom';
import SprayPanel from '../SprayPanel';

const ZONES = { water: [], trees: [], buildings: [], powerline: [], holes: [] };

const PLAN = {
  combined_waypoints: [{ lat: 39.9, lon: -95.8, alt: 100 },
                       { lat: 39.901, lon: -95.8, alt: 100 }],
  combined_leg_kinds: ['spray'],
  fields: [{ index: 0, waypoints: [], stats: {} }],
  flight_order: [{ index: 0, reversed: false }],
  transits: [],
  totals: { fields: 1, area_acres: 10, est_time_s: 60, spray_path_m: 100,
            transit_m: 0, waypoints: 2, keepout_overflights: 0 },
  zones: ZONES,
};

function renderPanel(overrides = {}) {
  const props = {
    connected: true, plan: PLAN, setPlan: vi.fn(),
    zones: ZONES, setZones: vi.fn(), homePos: null,
    fields: [], setFields: vi.fn(), draft: [], setDraft: vi.fn(),
    area: [], setArea: vi.fn(),
    drawing: false, setDrawing: vi.fn(),
    areaDrawing: false, setAreaDrawing: vi.fn(),
    snapping: false, setSnapping: vi.fn(), snapStatus: null,
    ...overrides,
  };
  return render(<SprayPanel {...props} />);
}

describe('arming the proximity monitor on upload', () => {
  let calls;
  beforeEach(() => {
    calls = [];
    global.fetch = vi.fn(async (url, opts) => {
      calls.push({ url: String(url), body: opts && opts.body ? JSON.parse(opts.body) : null });
      if (String(url).includes('/mission/upload')) {
        return { ok: true, json: async () => ({ count: 2 }) };
      }
      if (String(url).includes('/safety/keepouts')) {
        return { ok: true, json: async () => ({
          status: 'ok', hazards: 1, keepouts: 3, dropped: 0, hazard_buffer_m: 40 }) };
      }
      return { ok: true, json: async () => ({}) };
    });
  });

  const clickUpload = async () => {
    const btn = await screen.findByText(/Upload Mission/i);
    await act(async () => { fireEvent.click(btn); });
  };

  it('posts the planned zones to the monitor after a successful upload', async () => {
    renderPanel();
    await clickUpload();
    const armed = calls.find((c) => c.url.includes('/safety/keepouts'));
    expect(armed, 'upload must arm the proximity monitor').toBeTruthy();
    expect(armed.body.zones).toBeTruthy();
  });

  it('arms with the operator\'s buffer, not a hardcoded default', async () => {
    renderPanel();
    // Raise the powerline standoff before uploading.
    const input = screen.getByLabelText(/Powerline buf/i);
    await act(async () => { fireEvent.change(input, { target: { value: '40' } }); });
    await clickUpload();
    const armed = calls.find((c) => c.url.includes('/safety/keepouts'));
    expect(armed.body.hazard_buffer_m).toBe(40);
  });

  it('says so when arming FAILS instead of implying the monitor is watching', async () => {
    global.fetch = vi.fn(async (url) => {
      if (String(url).includes('/mission/upload')) {
        return { ok: true, json: async () => ({ count: 2 }) };
      }
      return { ok: false, json: async () => ({ detail: 'nope' }) };
    });
    renderPanel();
    await clickUpload();
    expect(await screen.findByText(/NOT armed/i)).toBeInTheDocument();
  });
});

/**
 * Rally points: the operator can place them, and the panel sends them.
 *
 * Seam S8b, and the half that makes S8a's read-out mean anything. `2a581b3`
 * built the whole link-loss diversion path — validate candidates against the
 * hazard rings, refuse any whose home<->rally leg is not clear, upload as
 * MISSION_TYPE_RALLY — and no frontend file ever sent `rally_points`. Zero hits
 * across frontend/src and web/src. So `rally.attempted` was false on every
 * flight for six weeks, and a link-loss RTL still flew straight home through a
 * mapped powerline, exactly as before the feature existed.
 *
 * Rally points are OPERATOR-PICKED by design: the backend builds the exclusion
 * fence from the rings on its own, but "somewhere safe to go instead" is a
 * location only a person who knows the ground can supply (routers/safety.py).
 * So the two properties worth pinning are that placement reaches the payload,
 * and that what comes back is rendered without the GCS adding a verdict of its
 * own (M6) — including the one that decides whether any of it works:
 * RALLY_INCL_HOME.
 */
import React from 'react';
import { render, screen, act, fireEvent } from '@testing-library/react';
import { vi } from 'vitest';
import '@testing-library/jest-dom';
import SprayPanel from '../SprayPanel';

const ZONES = { water: [], trees: [], buildings: [], powerline: [], holes: [] };

const PLAN = {
  combined_waypoints: [{ lat: 39.9, lon: -95.8, alt: 20 },
                       { lat: 39.901, lon: -95.8, alt: 20 }],
  combined_leg_kinds: ['spray'],
  fields: [{ index: 0, waypoints: [], stats: {} }],
  flight_order: [{ index: 0, reversed: false }],
  transits: [],
  totals: { fields: 1, area_acres: 10, est_time_s: 60, spray_path_m: 100,
            transit_m: 0, waypoints: 2, keepout_overflights: 0 },
  zones: ZONES,
};

const RALLY = [{ lat: 39.905, lon: -95.81, alt: 80 }];

/** Same success vocabulary S8a pinned. Reused deliberately: a rally block that
 *  cannot claim the aircraft holds something must not be able to borrow the
 *  wording from the block next to it either. */
const SUCCESS_TOKENS =
  /✓|accepted by the aircraft|enforced with no link|diverts to a point/i;

function renderPanel(overrides = {}) {
  const props = {
    connected: true, plan: PLAN, setPlan: vi.fn(),
    zones: ZONES, setZones: vi.fn(),
    homePos: { lat: 39.9042, lon: -95.7997 },
    fields: [], setFields: vi.fn(), draft: [], setDraft: vi.fn(),
    area: [], setArea: vi.fn(),
    drawing: false, setDrawing: vi.fn(),
    areaDrawing: false, setAreaDrawing: vi.fn(),
    snapping: false, setSnapping: vi.fn(), snapStatus: null,
    rallyPoints: [], setRallyPoints: vi.fn(),
    rallyPlacing: false, setRallyPlacing: vi.fn(),
    ...overrides,
  };
  return { ...render(<SprayPanel {...props} />), props };
}

function mockUpload(keepouts) {
  const calls = [];
  global.fetch = vi.fn(async (url, opts) => {
    calls.push({ url: String(url),
                 body: opts && opts.body ? JSON.parse(opts.body) : null });
    if (String(url).includes('/mission/upload')) {
      return { ok: true, json: async () => ({ count: 2 }) };
    }
    if (String(url).includes('/safety/keepouts')) {
      return { ok: true, json: async () => ({
        status: 'ok', hazards: 1, keepouts: 0, dropped: 0, hazard_buffer_m: 20,
        fence: { attempted: true, ok: true, ack: 0, points: 4, polygons: 1,
                 not_fenced: 0 },
        ...keepouts }) };
    }
    return { ok: true, json: async () => ({}) };
  });
  return calls;
}

const clickUpload = async () => {
  const btn = await screen.findByText(/Upload Mission/i);
  await act(async () => { fireEvent.click(btn); });
};

/** Upload with `rally` as the backend's rally result block. */
async function uploadWith(rally, overrides = {}) {
  const calls = mockUpload({ rally });
  const r = renderPanel({ rallyPoints: RALLY, ...overrides });
  await clickUpload();
  const armed = calls.find((c) => c.url.includes('/safety/keepouts'));
  return {
    ...r,
    calls,
    armed,
    rallyLine: r.container.querySelector('.rally-line'),
  };
}

describe('placing rally points', () => {
  it('arms placement as a mode, exclusive with the boundary tools', () => {
    // App.jsx routes ONE map click handler, so two armed modes would race for
    // the same click.
    const { props } = renderPanel();
    fireEvent.click(screen.getByText(/Place rally point/i));
    expect(props.setRallyPlacing).toHaveBeenCalledWith(true);
    expect(props.setDrawing).toHaveBeenCalledWith(false);
    expect(props.setAreaDrawing).toHaveBeenCalledWith(false);
    expect(props.setSnapping).toHaveBeenCalledWith(false);
  });

  it('turns rally placement OFF when a boundary tool is armed', () => {
    const { props } = renderPanel({ rallyPlacing: true });
    fireEvent.click(screen.getByText(/Draw/i));
    expect(props.setRallyPlacing).toHaveBeenCalledWith(false);
  });

  it('lists each placed point with its position and an editable altitude', () => {
    renderPanel({ rallyPoints: RALLY });
    expect(screen.getByText('R1')).toBeInTheDocument();
    expect(screen.getByText(/39\.90500, -95\.81000/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Rally 1 altitude/i)).toHaveValue(80);
  });

  it('removes a point without touching the rest of the job', () => {
    const { props } = renderPanel({
      rallyPoints: [...RALLY, { lat: 39.906, lon: -95.812, alt: 80 }],
    });
    fireEvent.click(screen.getByLabelText(/Remove rally 1/i));
    expect(props.setRallyPoints).toHaveBeenCalledWith([
      { lat: 39.906, lon: -95.812, alt: 80 },
    ]);
    expect(props.setPlan).not.toHaveBeenCalled();
  });

  it('says out loud when none are placed, rather than leaving it blank', () => {
    // The state this seam actually shipped in.
    renderPanel({ rallyPoints: [] });
    expect(screen.getByText(/None placed/i)).toBeInTheDocument();
    expect(screen.getByText(/straight line home/i)).toBeInTheDocument();
  });
});

describe('sending them with the keepouts', () => {
  it('puts the placed points in the POST body', async () => {
    const { armed } = await uploadWith({ attempted: true, ok: true, points: 1 });
    expect(armed.body.rally_points).toEqual([
      { lat: 39.905, lon: -95.81, alt: 80 },
    ]);
  });

  it('sends lat/lon/alt ONLY — the FC discards break_alt and land_dir',
    async () => {
      // ArduPilot's rally item conversion keeps x/y/z only, so those two fields
      // never round-trip (LANES.md decisions log, 2026-08-19). Sending them
      // would invite an editor for values the aircraft throws away.
      const { armed } = await uploadWith({ attempted: true, ok: true, points: 1 });
      expect(Object.keys(armed.body.rally_points[0]).sort())
        .toEqual(['alt', 'lat', 'lon']);
    });

  it('sends an empty list when none are placed, not a fabricated default',
    async () => {
      const calls = mockUpload({ rally: { attempted: false } });
      renderPanel({ rallyPoints: [] });
      await clickUpload();
      const armed = calls.find((c) => c.url.includes('/safety/keepouts'));
      expect(armed.body.rally_points).toEqual([]);
    });
});

describe('rendering what came back, without adding a verdict', () => {
  it('renders a backend refusal VERBATIM', async () => {
    // M6: the GCS holds no thresholds and computes no readiness. The clearance
    // number and the reason are the backend's to state.
    const error = 'candidate 0 (39.905000, -95.810000): the home<->rally leg '
                + 'passes within 88 m of a powerline hazard, under the 150 m '
                + 'clearance';
    const { rallyLine } = await uploadWith({ attempted: true, ok: false, error });
    expect(rallyLine.textContent).toContain(error);
    expect(rallyLine.className).toMatch(/\berr\b/);
    expect(rallyLine.textContent).not.toMatch(SUCCESS_TOKENS);
  });

  it('confirms the diversion only when the FC prefers rally over home',
    async () => {
      const { rallyLine } = await uploadWith({
        attempted: true, ok: true, points: 1,
        incl_home: { known: true, value: 0, diverts_to_rally: true, warning: null },
      });
      expect(rallyLine.className).toMatch(/\bok\b/);
      expect(rallyLine.textContent).toMatch(/1 accepted by the aircraft/i);
    });

  it('RALLY_INCL_HOME=1 never reads as a working diversion', async () => {
    // The failure this whole parameter row exists for: the points upload fine,
    // every check passes, and the aircraft still flies home through the wire
    // because home is back in the running as a candidate.
    const warning = 'RALLY_INCL_HOME=1 on this aircraft, so home counts as a '
                  + 'rally candidate and a link-loss RTL may fly straight home '
                  + 'THROUGH the hazards these rally points exist to avoid. '
                  + 'The product does not change this parameter.';
    const { rallyLine } = await uploadWith({
      attempted: true, ok: true, points: 1,
      incl_home: { known: true, value: 1, diverts_to_rally: false, warning },
    });
    expect(rallyLine.className).not.toMatch(/\bok\b/);
    expect(rallyLine.textContent).toMatch(/not confirmed/i);
    expect(rallyLine.textContent).toContain(warning);
    expect(rallyLine.textContent).not.toMatch(SUCCESS_TOKENS);
  });

  it('an UNREADABLE RALLY_INCL_HOME is not a working diversion either',
    async () => {
      // known:false means we cannot judge, which lands in the same branch as
      // the bad value on purpose — the read-out may not claim the diversion
      // works, so it does not.
      const warning = 'the aircraft did not answer for RALLY_INCL_HOME -- '
                    + 'cannot tell whether a link-loss RTL will prefer the '
                    + 'rally point over home.';
      const { rallyLine } = await uploadWith({
        attempted: true, ok: true, points: 1,
        incl_home: { known: false, value: null, diverts_to_rally: null, warning },
      });
      expect(rallyLine.className).not.toMatch(/\bok\b/);
      expect(rallyLine.textContent).toContain(warning);
      expect(rallyLine.textContent).not.toMatch(SUCCESS_TOKENS);
    });

  it('a stale result is cleared when the rally set changes underneath it',
    async () => {
      // The rendered block describes the set we SENT. Leaving it up after an
      // edit would say the aircraft holds points it was never given.
      const { container } = await uploadWith({
        attempted: true, ok: true, points: 1,
        incl_home: { known: true, value: 0, diverts_to_rally: true, warning: null },
      });
      expect(container.querySelector('.rally-line')).toBeTruthy();
      await act(async () => {
        fireEvent.click(screen.getByLabelText(/Remove rally 1/i));
      });
      expect(container.querySelector('.rally-line')).toBeNull();
      expect(container.querySelector('.fence-line')).toBeNull();
    });
});

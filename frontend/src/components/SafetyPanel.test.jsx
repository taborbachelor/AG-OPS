/**
 * The safety read-back: what the vehicle ACTUALLY holds.
 *
 * Four endpoints were built so the GCS could stop reporting what it last SENT
 * -- GET /safety/keepouts, /exclusions, /rally, /guardian -- and until now not
 * one of them had a UI caller. seam_check --ui listed all four as unreachable
 * by any operator.
 *
 * These tests pin the one invariant the whole surface exists for: AN UNKNOWN
 * MUST NEVER RENDER AS AN ALL-CLEAR. There are four distinct ways of not
 * knowing, and each is a separate trap:
 *
 *   known: false        the proximity monitor holds no rings, so it cannot judge
 *   supported: false    the link cannot address the FENCE/RALLY mission type
 *   not connected       /exclusions answers `points: 0` with no vehicle attached,
 *                       which is indistinguishable from a genuinely empty fence
 *   read failed         a 500 or a dropped fetch
 *
 * Any of those rendering as "none held" tells an operator a surveyed powerline
 * is protected when nothing on the aircraft knows about it.
 *
 * (Co-located rather than under __tests__/ on purpose: TASK-019 owns that
 * directory's glob this wave. Same pattern as ConnectionOverlay.test.jsx.)
 */
import React from 'react';
import { render, screen, act, fireEvent } from '@testing-library/react';
import { vi } from 'vitest';
import '@testing-library/jest-dom';
import SafetyPanel, {
  UNKNOWN, HELD, CLEAR, INFO, ALERT,
  monitorState, heldState, fenceMismatch, guardianStatus,
} from './SafetyPanel';

const GUARDIAN = {
  config: {
    enabled: true, gps_min_sats: 6, gps_action: 'warn',
    batt_warn_volt: 10.8, batt_rtl_volt: 10.4, batt_action: 'rtl',
    bank_warn_deg: 45, bank_action: 'warn', bank_low_alt_m: 30,
    keepout_action: 'warn', keepout_sustained_s: 3,
  },
  state: { state: 'NORMAL', monitors: {}, warnings: [] },
};

const ARMED = { known: true, n_hazards: 2, n_keepouts: 5, hazard_buffer_m: 20, dropped: 0 };
const FENCE_HELD = { supported: true, reason: null, points: 12, items: [] };
const RALLY_HELD = { supported: true, reason: null, points: 2, items: [] };

/** Endpoint -> body. A `null` body means respond !ok (a failed read). */
function mockApi(routes = {}) {
  const table = {
    '/safety/geofence': { enable: true, radius: 300, alt_max: 120, action: 1 },
    '/safety/failsafe': {
      batt_low_volt: 10.5, batt_low_action: 2, batt_crit_volt: 10.0,
      batt_crit_action: 1, gcs_enable: 1, rc_enable: true, rc_long_action: 1,
    },
    '/safety/keepouts': ARMED,
    '/safety/exclusions': FENCE_HELD,
    '/safety/rally': RALLY_HELD,
    '/safety/guardian': GUARDIAN,
    ...routes,
  };
  const seen = [];
  global.fetch = vi.fn(async (url) => {
    const u = String(url);
    seen.push(u);
    const key = Object.keys(table).find((k) => u.includes(k));
    const body = key ? table[key] : {};
    if (body === null) return { ok: false, status: 500, json: async () => ({}) };
    return { ok: true, json: async () => body };
  });
  return seen;
}

async function renderPanel({ connected = true, routes = {} } = {}) {
  const seen = mockApi(routes);
  let out;
  await act(async () => {
    out = render(<SafetyPanel connected={connected} setFence={vi.fn()}
      fence={{ enable: true, radius: 300, alt_max: 120, action: 1 }} />);
  });
  return { ...out, seen };
}

/** Words that would tell an operator "nothing to worry about". */
const CLEAR_WORDS = /\b(none|clear|no hazards|0 points|ok)\b/i;

describe('the read-back endpoints have an operator-reachable caller at all', () => {
  it('calls all four on mount while connected', async () => {
    const { seen } = await renderPanel();
    for (const path of ['/safety/keepouts', '/safety/exclusions',
                        '/safety/rally', '/safety/guardian']) {
      expect(seen.some((u) => u.includes(path)), `${path} must have a UI caller`).toBe(true);
    }
  });

  it('renders what the vehicle holds, distinctly from the GCS monitor', async () => {
    await renderPanel();
    // GCS-side monitor: soft, dies with the link.
    expect(screen.getByText(/2 hazard rings, 5 keepouts, 20 m buffer/)).toBeInTheDocument();
    // Onboard: survives link loss.
    expect(screen.getByText(/12 fence points held onboard/)).toBeInTheDocument();
    expect(screen.getByText(/2 rally points held onboard/)).toBeInTheDocument();
    // The distinction itself must be on screen, not implied by layout.
    expect(screen.getByText(/survives link loss/i)).toBeInTheDocument();
  });

  it('renders the guardian config and live state', async () => {
    await renderPanel();
    expect(screen.getByText(/enabled . NORMAL/)).toBeInTheDocument();
    expect(screen.getByText(/10\.8 V \/ 10\.4 V \(rtl\)/)).toBeInTheDocument();
    expect(screen.getByText(/45. \(warn\), tightened below 30 m/)).toBeInTheDocument();
  });
});

describe('an unknown never reads as an all-clear', () => {
  it('known:false shows UNKNOWN, not an empty monitor', async () => {
    await renderPanel({ routes: { '/safety/keepouts': { known: false, n_hazards: 0, n_keepouts: 0 } } });
    const row = screen.getByText(/not being judged/i);
    expect(row).toHaveTextContent(/UNKNOWN/);
    expect(row.textContent).not.toMatch(CLEAR_WORDS);
  });

  it('supported:false names the reason and never says none held', async () => {
    await renderPanel({ routes: {
      '/safety/exclusions': {
        supported: false,
        reason: 'link is bound to MAVLink 1 bindings, which have no mission_type field',
        points: 0, items: [],
      },
    } });
    const row = screen.getByText(/cannot read back/i);
    expect(row).toHaveTextContent(/UNKNOWN/);
    expect(row).toHaveTextContent(/MAVLink 1/);
    expect(screen.queryByText(/fence points held onboard/)).not.toBeInTheDocument();
    expect(screen.queryAllByText(/none held onboard/).length).toBe(0);
  });

  it('disconnected shows UNKNOWN -- points:0 off a dead link is not an empty fence', async () => {
    // The endpoint would answer this exact body with no vehicle attached.
    await renderPanel({ connected: false, routes: {
      '/safety/exclusions': { supported: true, reason: null, points: 0, items: [] },
      '/safety/rally': { supported: true, reason: null, points: 0, items: [] },
    } });
    const rows = screen.getAllByText(/no link, cannot read the vehicle/i);
    expect(rows.length).toBe(2);           // fence AND rally
    rows.forEach((r) => expect(r).toHaveTextContent(/UNKNOWN/));
    expect(screen.queryAllByText(/none held onboard/).length).toBe(0);
  });

  it('does not even ask the vehicle read-backs while disconnected', async () => {
    const { seen } = await renderPanel({ connected: false });
    expect(seen.some((u) => u.includes('/safety/exclusions'))).toBe(false);
    expect(seen.some((u) => u.includes('/safety/rally'))).toBe(false);
    // GCS-side surfaces still answer with no vehicle, so they are still read.
    expect(seen.some((u) => u.includes('/safety/keepouts'))).toBe(true);
    expect(seen.some((u) => u.includes('/safety/guardian'))).toBe(true);
  });

  it('a failed read shows UNKNOWN rather than an empty result', async () => {
    await renderPanel({ routes: {
      '/safety/exclusions': null,   // 500
      '/safety/rally': null,
      '/safety/keepouts': null,
      '/safety/guardian': null,
    } });
    expect(screen.getAllByText(/read-back failed/i).length).toBe(2);
    expect(screen.getByText(/UNKNOWN . read failed/)).toBeInTheDocument();
    expect(screen.getByText(/guardian read failed/i)).toBeInTheDocument();
    expect(screen.queryAllByText(/held onboard/).length).toBe(0);
  });

  it('a good reading does not survive a failed refresh as if it were current', async () => {
    await renderPanel();
    expect(screen.getByText(/12 fence points held onboard/)).toBeInTheDocument();

    mockApi({ '/safety/exclusions': null });
    await act(async () => { fireEvent.click(screen.getByText('Re-read')); });

    expect(screen.queryByText(/12 fence points held onboard/)).not.toBeInTheDocument();
    expect(screen.getByText(/read-back failed/i)).toBeInTheDocument();
  });
});

describe('sent is not held', () => {
  it('warns when the GCS is armed with hazards the aircraft has no fence for', async () => {
    await renderPanel({ routes: {
      '/safety/exclusions': { supported: true, reason: null, points: 0, items: [] },
    } });
    const warn = screen.getByText(/SENT . HELD/);
    expect(warn).toHaveTextContent(/2 hazard/);
    expect(warn).toHaveTextContent(/only while the link is up/i);
  });

  it('stays quiet when the fence is actually held', async () => {
    await renderPanel();
    expect(screen.queryByText(/SENT . HELD/)).not.toBeInTheDocument();
  });

  it('does not fire off an UNKNOWN fence -- that is a different problem', async () => {
    await renderPanel({ connected: false });
    expect(screen.queryByText(/SENT . HELD/)).not.toBeInTheDocument();
  });
});

// Mutation check: the invariant stated as a property over every not-knowing
// input, so a change that starts reporting one of them as empty fails here
// even if it renders prettily.
describe('invariant: every way of not knowing yields UNKNOWN', () => {
  const notKnowing = [
    ['no response', () => monitorState(null)],
    ['known:false', () => monitorState({ known: false, n_hazards: 0, n_keepouts: 0 })],
    ['known missing', () => monitorState({ n_hazards: 0, n_keepouts: 0 })],
    ['disconnected', () => heldState(false, { supported: true, points: 0 }, 'fence points')],
    ['disconnected w/ points', () => heldState(false, { supported: true, points: 9 }, 'fence points')],
    ['read failed', () => heldState(true, null, 'fence points')],
    ['unsupported', () => heldState(true, { supported: false, reason: 'MAVLink 1', points: 0 }, 'fence points')],
    ['unsupported, no reason', () => heldState(true, { supported: false, points: 0 }, 'fence points')],
  ];

  notKnowing.forEach(([name, fn]) => {
    it(`${name} -> UNKNOWN, and says so in words`, () => {
      const s = fn();
      expect(s.level).toBe(UNKNOWN);
      expect(s.text).toMatch(/UNKNOWN/);
      expect(s.text.replace(/UNKNOWN/g, '')).not.toMatch(CLEAR_WORDS);
    });
  });

  it('only a positive read of zero is reported as zero', () => {
    expect(heldState(true, { supported: true, points: 0 }, 'fence points').level).toBe(CLEAR);
    expect(heldState(true, { supported: true, points: 4 }, 'fence points').level).toBe(HELD);
    expect(monitorState({ known: true, n_hazards: 0, n_keepouts: 0 }).level).toBe(CLEAR);
    expect(monitorState({ known: true, n_hazards: 1, n_keepouts: 0 }).level).toBe(HELD);
  });

  it('a guardian that is off, or silent, is not an all-clear either', () => {
    const on = { enabled: true };
    expect(guardianStatus({ enabled: false }, { state: 'NORMAL' }).level).toBe(UNKNOWN);
    expect(guardianStatus(on, null).level).toBe(UNKNOWN);
    expect(guardianStatus(on, {}).level).toBe(UNKNOWN);
    // ...and a state the guardian DID report is known, not unknown.
    expect(guardianStatus(on, { state: 'NORMAL' }).level).toBe(HELD);
    expect(guardianStatus(on, { state: 'DISARMED' }).level).toBe(INFO);
    ['WARNING', 'RTL_REQUESTED', 'RTL_ACTIVE', 'LANDING'].forEach((s) => {
      const v = guardianStatus(on, { state: s });
      expect(v.level).toBe(ALERT);
      expect(v.text).toContain(s);
    });
  });

  it('the mismatch warning needs a POSITIVE empty read, never an unknown one', () => {
    const armed = { known: true, n_hazards: 2 };
    expect(fenceMismatch(armed, heldState(true, { supported: true, points: 0 }, 'p'))).toBe(true);
    expect(fenceMismatch(armed, heldState(false, { supported: true, points: 0 }, 'p'))).toBe(false);
    expect(fenceMismatch(armed, heldState(true, null, 'p'))).toBe(false);
    expect(fenceMismatch(armed, heldState(true, { supported: false, points: 0 }, 'p'))).toBe(false);
    expect(fenceMismatch({ known: false }, heldState(true, { supported: true, points: 0 }, 'p'))).toBe(false);
  });
});

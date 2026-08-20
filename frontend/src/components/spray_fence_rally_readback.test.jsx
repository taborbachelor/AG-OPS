/**
 * LOCATION NOTE: this file belongs in `__tests__/`, and sits here only because
 * TASK-019 and TASK-021 were dispatched with the SAME wildcard file entry
 * (`frontend/src/components/__tests__/*`), which makes the AgOps PreToolUse
 * guard refuse a new file in that directory to BOTH owners at once. alpha and
 * bravo agreed the filenames in writing instead; going around the directory is
 * rule 2's "go around it", not a forced write. Move it with `git mv` into
 * `__tests__/` once TASK-021 completes, and change the import back to
 * `../SprayPanel`.
 */
/**
 * The onboard exclusion fence and rally points must state their OWN outcome.
 *
 * Seam S8. `POST /api/safety/keepouts` has always returned a `fence` block
 * ({attempted, ok, error, polygons, not_fenced}) and a `rally` block
 * ({attempted, ok, error, points}). SprayPanel.jsx referenced neither: it read
 * `armed.hazards` / `armed.keepouts` and printed "proximity monitor armed" from
 * those ring counts alone. So a surveyed powerline that could NOT be fenced
 * looked, to the operator, exactly like one that was — and `rally.attempted`
 * was false on every flight without anybody being told.
 *
 * The soft GCS monitor and the hard onboard fence are DIFFERENT protections.
 * The monitor runs on the laptop and goes silent when the link drops; the fence
 * is the one the autopilot enforces with no link at all. These tests pin the
 * single invariant that difference implies:
 *
 *     anything that is not an upload the AIRCRAFT accepted reads as unprotected.
 *
 * Most of them are mutation checks rather than text checks — they assert that
 * the failure states cannot be made to read as success, which is the property
 * that actually failed here, not the wording.
 */
import React from 'react';
import { render, screen, act, fireEvent } from '@testing-library/react';
import { vi } from 'vitest';
import '@testing-library/jest-dom';
import SprayPanel from './SprayPanel';

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

/** Every phrase this panel uses to say "the aircraft is holding it". If a
 *  refactor ever lets one of these appear on a state the aircraft did NOT
 *  accept, that is the S8 bug returning, and these tests fail. */
const SUCCESS_TOKENS =
  /✓|accepted by the aircraft|enforced with no link|diverts to a point/i;

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

/** Mock the two calls an upload makes. `keepouts` is the safety response body;
 *  pass `{ __fail: <code> }` to make that call fail outright. */
function mockUpload(keepouts) {
  global.fetch = vi.fn(async (url) => {
    if (String(url).includes('/mission/upload')) {
      return { ok: true, json: async () => ({ count: 2 }) };
    }
    if (String(url).includes('/safety/keepouts')) {
      if (keepouts && keepouts.__fail) {
        return { ok: false, status: keepouts.__fail,
                 json: async () => ({ detail: keepouts.__detail }) };
      }
      return { ok: true, json: async () => ({
        status: 'ok', hazards: 1, keepouts: 3, dropped: 0, hazard_buffer_m: 20,
        ...keepouts }) };
    }
    return { ok: true, json: async () => ({}) };
  });
}

const clickUpload = async () => {
  const btn = await screen.findByText(/Upload Mission/i);
  await act(async () => { fireEvent.click(btn); });
};

/** Render, upload, and hand back the fence and rally read-out elements. */
async function upload(keepouts, overrides) {
  mockUpload(keepouts);
  const { container } = renderPanel(overrides);
  await clickUpload();
  return {
    container,
    fence: container.querySelector('.fence-line'),
    rally: container.querySelector('.rally-line'),
    notFenced: container.querySelector('.fence-notfenced'),
  };
}

const OK_FENCE = { attempted: true, ok: true, ack: 0, points: 8,
                   polygons: 2, not_fenced: 0 };
const NO_RALLY = { attempted: false };

describe('the onboard exclusion fence states its own outcome', () => {
  it('reports an accepted fence as enforced, with the polygon count', async () => {
    const { fence } = await upload({ fence: OK_FENCE, rally: NO_RALLY });
    expect(fence).toBeTruthy();
    expect(fence.className).toMatch(/\bok\b/);
    expect(fence.textContent).toMatch(/2 hazard polygon/i);
    expect(fence.textContent).toMatch(/accepted by the aircraft/i);
  });

  it('a fence the backend REFUSED to build never reads as protected', async () => {
    // build_exclusion_items raising: home inside an exclusion, an over-budget
    // ring, a pathological ring. No polygons/not_fenced keys at all.
    const { fence } = await upload({
      fence: { attempted: true, ok: false,
               error: 'home is inside or within 30 m of a powerline exclusion' },
      rally: NO_RALLY,
    });
    expect(fence.className).toMatch(/\berr\b/);
    expect(fence.textContent).toMatch(/FAILED/);
    expect(fence.textContent).toMatch(/home is inside/i);
    expect(fence.textContent).toMatch(/NOT enforced onboard/i);
    expect(fence.textContent).not.toMatch(SUCCESS_TOKENS);
  });

  it('polygons BUILT but rejected by the vehicle are not counted as protection',
    async () => {
      // The trap: safety.py assigns fence.polygons AFTER the upload result, so
      // a failed transfer still reports "polygons: 3". Printing that count
      // without the ok flag is precisely how a dead fence reads as a live one.
      const { fence } = await upload({
        fence: { attempted: true, ok: false, ack: 1, points: 12, polygons: 3,
                 not_fenced: 0,
                 error: 'fence transfer timed out (no request/ack from vehicle)' },
        rally: NO_RALLY,
      });
      expect(fence.className).toMatch(/\berr\b/);
      expect(fence.textContent).toMatch(/3 hazard polygon\(s\) were built/i);
      expect(fence.textContent).toMatch(/did NOT accept them/i);
      expect(fence.textContent).toMatch(/NOT enforced onboard/i);
      expect(fence.textContent).not.toMatch(SUCCESS_TOKENS);
    });

  it('a fence that was never attempted says so, and says what is left watching',
    async () => {
      // attempted:false = disconnected, or push_to_vehicle off. The hazards
      // exist only in the GCS monitor, which dies with the link.
      const { fence } = await upload({
        fence: { attempted: false }, rally: NO_RALLY,
      });
      expect(fence.className).toMatch(/\berr\b/);
      expect(fence.textContent).toMatch(/NOT pushed to the aircraft/i);
      expect(fence.textContent).toMatch(/exist only in the GCS monitor/i);
      expect(fence.textContent).not.toMatch(SUCCESS_TOKENS);
    });

  it('ok with ZERO polygons is a fence CLEAR, not a fence armed', async () => {
    // An empty transfer is a legitimate success that removes the vehicle's
    // fence. It is the one ok:true that protects nothing.
    const { fence } = await upload({
      fence: { attempted: true, ok: true, ack: 0, points: 0, polygons: 0,
               not_fenced: 4 },
      rally: NO_RALLY,
    });
    expect(fence.className).not.toMatch(/\bok\b/);
    expect(fence.textContent).toMatch(/CLEARED/);
    expect(fence.textContent).toMatch(/Nothing is fenced onboard/i);
    expect(fence.textContent).not.toMatch(SUCCESS_TOKENS);
  });

  it('names WHY the not_fenced rings were left out, rather than hiding them',
    async () => {
      const { notFenced } = await upload({
        fence: { ...OK_FENCE, not_fenced: 4 }, rally: NO_RALLY,
      });
      expect(notFenced).toBeTruthy();
      expect(notFenced.textContent).toMatch(/4 spray-quality keepout/i);
      expect(notFenced.textContent).toMatch(/water, trees, buildings/i);
      expect(notFenced.textContent).toMatch(/failsafe/i);
    });

  it('says nothing about not_fenced when there is nothing to say', async () => {
    const { notFenced } = await upload({ fence: OK_FENCE, rally: NO_RALLY });
    expect(notFenced).toBeNull();
  });
});

describe('rally points state their own outcome', () => {
  it('renders attempted:false honestly as none loaded', async () => {
    // Today's every-flight case: nothing in the GCS sends rally candidates
    // yet. Silence here is what let the feature ship dead.
    const { rally } = await upload({ fence: OK_FENCE, rally: { attempted: false } });
    expect(rally.textContent).toMatch(/none loaded/i);
    expect(rally.textContent).toMatch(/straight line home/i);
    expect(rally.textContent).toMatch(/NOT divert/i);
    expect(rally.textContent).not.toMatch(SUCCESS_TOKENS);
  });

  it('a refused rally upload never reads as a diversion the aircraft has',
    async () => {
      const { rally } = await upload({
        fence: OK_FENCE,
        rally: { attempted: true, ok: false,
                 error: 'candidate 0 (39.900000, -95.800000): 40 m from a '
                        + 'powerline hazard, under the 150 m clearance' },
      });
      expect(rally.className).toMatch(/\berr\b/);
      expect(rally.textContent).toMatch(/FAILED/);
      expect(rally.textContent).toMatch(/under the 150 m clearance/i);
      expect(rally.textContent).toMatch(/straight line home/i);
      expect(rally.textContent).not.toMatch(SUCCESS_TOKENS);
    });

  it('reports accepted rally points with the count the vehicle took', async () => {
    const { rally } = await upload({
      fence: OK_FENCE,
      rally: { attempted: true, ok: true, ack: 0, points: 2 },
    });
    expect(rally.className).toMatch(/\bok\b/);
    expect(rally.textContent).toMatch(/2 accepted by the aircraft/i);
  });

  it('an accepted transfer holding zero points is not a diversion', async () => {
    const { rally } = await upload({
      fence: OK_FENCE,
      rally: { attempted: true, ok: true, ack: 0, points: 0 },
    });
    expect(rally.className).not.toMatch(/\bok\b/);
    expect(rally.textContent).toMatch(/holds NONE/i);
    expect(rally.textContent).not.toMatch(SUCCESS_TOKENS);
  });
});

describe('when the safety call itself fails', () => {
  it('reports the fence and rally state as UNKNOWN, never as clear', async () => {
    // The same call arms the monitor AND pushes the fence, so losing it leaves
    // both unknown. Unknown is not "no hazards".
    const { fence, rally } = await upload({ __fail: 500, __detail: 'boom' });
    expect(fence.className).toMatch(/\berr\b/);
    expect(fence.textContent).toMatch(/UNKNOWN/);
    expect(fence.textContent).toMatch(/boom/);
    expect(fence.textContent).toMatch(/Do not assume/i);
    expect(fence.textContent).not.toMatch(SUCCESS_TOKENS);
    // One combined statement, not a second block implying rally was checked.
    expect(rally).toBeNull();
  });

  it('still says the monitor is not armed', async () => {
    await upload({ __fail: 500, __detail: 'boom' });
    expect(await screen.findByText(/NOT armed/i)).toBeInTheDocument();
  });
});

describe('the monitor line does not speak for the aircraft', () => {
  it('marks the armed monitor as GCS-side, so it cannot read as onboard cover',
    async () => {
      const { container } = await upload({
        fence: { attempted: false }, rally: NO_RALLY,
      });
      const monitor = container.querySelector('.spray-status.ok');
      expect(monitor).toBeTruthy();
      expect(monitor.textContent).toMatch(/proximity monitor armed GCS-side/i);
      // The fence failed in this very render; the monitor line must not be
      // the only thing an operator reads.
      expect(container.textContent).toMatch(/NOT pushed to the aircraft/i);
    });
});

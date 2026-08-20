/**
 * The spray-plan altitude default, UI half (TASK-012, LANES seam S3).
 *
 * The backend request default only applies to API clients that omit `alt`.
 * This form ALWAYS sends it, so the number here is the one an operator
 * actually flies — changing only the backend would have left the fix
 * invisible, which is the exact shape of the cross-lane bug this project has
 * already been bitten by twice.
 */
import React from 'react';
import { render, screen, act, fireEvent } from '@testing-library/react';
import { vi } from 'vitest';
import '@testing-library/jest-dom';
import SprayPanel from '../SprayPanel';

// Must match coverage.py's DEFAULT_SPRAY_ALT_M.
const DEFAULT_SPRAY_ALT_M = 20;

function renderPanel(overrides = {}) {
  const props = {
    connected: true, plan: null, setPlan: vi.fn(),
    zones: null, setZones: vi.fn(), homePos: null,
    fields: [{ polygon: [{ lat: 39.9, lon: -95.8 }, { lat: 39.901, lon: -95.8 },
                          { lat: 39.901, lon: -95.801 }], acres: 10, source: 'drawn' }],
    setFields: vi.fn(), draft: [], setDraft: vi.fn(),
    area: [], setArea: vi.fn(),
    drawing: false, setDrawing: vi.fn(),
    areaDrawing: false, setAreaDrawing: vi.fn(),
    snapping: false, setSnapping: vi.fn(), snapStatus: null,
    ...overrides,
  };
  return render(<SprayPanel {...props} />);
}

describe('spray altitude default', () => {
  let calls;
  beforeEach(() => {
    calls = [];
    global.fetch = vi.fn(async (url, opts) => {
      calls.push({ url: String(url), body: opts && opts.body ? JSON.parse(opts.body) : null });
      return { ok: true, json: async () => ({ totals: { fields: 1 }, zones: null }) };
    });
  });
  afterEach(() => vi.restoreAllMocks());

  it('the altitude field opens at a real spray altitude, not the 100 m placeholder', () => {
    renderPanel();
    const field = screen.getByLabelText('Altitude');
    expect(Number(field.value)).toBe(DEFAULT_SPRAY_ALT_M);
    expect(Number(field.value)).not.toBe(100);
  });

  it('sends that altitude to the planner', async () => {
    // The assertion that matters: the default has to survive all the way into
    // the request body, not just render in a box.
    renderPanel();
    await act(async () => {
      fireEvent.click(await screen.findByText(/Generate Spray Plan/i));
    });
    const req = calls.find((c) => c.url.includes('/coverage/plan_multi'));
    expect(req.body.alt).toBe(DEFAULT_SPRAY_ALT_M);
  });

  it('the operator can still fly a plan higher', async () => {
    // Only the default changed. Flying a field high is a legitimate choice and
    // must not have been quietly removed.
    renderPanel();
    fireEvent.change(screen.getByLabelText('Altitude'), { target: { value: '60' } });
    await act(async () => {
      fireEvent.click(await screen.findByText(/Generate Spray Plan/i));
    });
    const req = calls.find((c) => c.url.includes('/coverage/plan_multi'));
    expect(req.body.alt).toBe(60);
  });
});

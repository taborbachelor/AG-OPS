/**
 * Pass widening (headlands) is on by default and lets spray reach up to half
 * a swath past the field boundary — load-bearing near an organic neighbour,
 * road, or waterway. Backend already accepted `headlands` on plan_multi; the
 * UI just never exposed it (seam S6). These tests pin that the toggle exists,
 * defaults on, and actually changes what /coverage/plan_multi is sent.
 */
import React from 'react';
import { render, screen, act, fireEvent } from '@testing-library/react';
import { vi } from 'vitest';
import '@testing-library/jest-dom';
import SprayPanel from '../SprayPanel';

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

describe('headland (pass widening) toggle', () => {
  let calls;
  beforeEach(() => {
    calls = [];
    global.fetch = vi.fn(async (url, opts) => {
      calls.push({ url: String(url), body: opts && opts.body ? JSON.parse(opts.body) : null });
      return { ok: true, json: async () => ({ totals: { fields: 1 }, zones: null }) };
    });
  });

  const clickGenerate = async () => {
    const btn = await screen.findByText(/Generate Spray Plan/i);
    await act(async () => { fireEvent.click(btn); });
  };

  it('defaults on and sends headlands: true', async () => {
    renderPanel();
    expect(screen.getByText(/Full boundary coverage/i)).toBeInTheDocument();
    await clickGenerate();
    const req = calls.find((c) => c.url.includes('/coverage/plan_multi'));
    expect(req.body.headlands).toBe(true);
  });

  it('flipping the toggle off sends headlands: false and warns about the uncovered strip', async () => {
    renderPanel();
    const toggle = screen.getByRole('checkbox');
    await act(async () => { fireEvent.click(toggle); });
    expect(screen.getByText(/thin strip near the edge may go uncovered/i)).toBeInTheDocument();
    await clickGenerate();
    const req = calls.find((c) => c.url.includes('/coverage/plan_multi'));
    expect(req.body.headlands).toBe(false);
  });
});

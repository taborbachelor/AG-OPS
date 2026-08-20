/**
 * Auto-connect the Cube by USB VID (TASK-007) — the App-level loop.
 *
 * The point of the feature is that nobody presses anything, so the behaviour
 * cannot be tested through the connection dialog: these cover that the probe
 * runs on its own, keeps running while nothing is plugged in, and knows when
 * to give up rather than re-dial a silent vehicle forever.
 */
import React from 'react';
import { render } from '@testing-library/react';
import { vi } from 'vitest';
import '@testing-library/jest-dom';

// Same shell mocks as the smoke test: react-leaflet v5 is ESM-only and the
// video feed wants real media devices. Neither is under test here.
vi.mock('./components/MapView', () => ({ default: () => null }));
vi.mock('./components/MapView3D', () => ({ default: () => null }));
vi.mock('./components/VideoFeed', () => ({ default: () => null }));

import App from './App';

const AUTOCONNECT = '/connection/autoconnect';

/** Backend where /connection/autoconnect answers with `replies` in order (the
 *  last one repeating), and everything else is an empty 200. */
function mockBackend(replies) {
  let i = 0;
  return vi.fn((url) => {
    if (String(url).endsWith(AUTOCONNECT)) {
      const r = replies[Math.min(i++, replies.length - 1)];
      return Promise.resolve({
        ok: r.status === 200,
        status: r.status,
        json: () => Promise.resolve(r.body || {}),
      });
    }
    if (String(url).endsWith('/connection/status')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ connected: false, reconnecting: false }),
      });
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
  });
}

const attempts = (f) =>
  f.mock.calls.filter(([url]) => String(url).endsWith(AUTOCONNECT)).length;

describe('App auto-connect', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  test('dials the board on startup with no operator action', async () => {
    // Nothing was clicked. That is the whole feature.
    const fetchMock = mockBackend([{ status: 200, body: { status: 'connected' } }]);
    global.fetch = fetchMock;

    render(<App />);
    await vi.advanceTimersByTimeAsync(0);

    expect(attempts(fetchMock)).toBe(1);
    const [, opts] = fetchMock.mock.calls.find(([u]) => String(u).endsWith(AUTOCONNECT));
    expect(opts.method).toBe('POST');
  });

  test('keeps probing while nothing is plugged in', async () => {
    // 404 = no flight controller on USB. The operator may still be walking
    // back from the aircraft with the cable, so this must not be terminal.
    const fetchMock = mockBackend([{ status: 404, body: { detail: 'No flight controller on USB' } }]);
    global.fetch = fetchMock;

    render(<App />);
    await vi.advanceTimersByTimeAsync(0);
    expect(attempts(fetchMock)).toBe(1);

    await vi.advanceTimersByTimeAsync(4000);
    expect(attempts(fetchMock)).toBe(2);
  });

  test('stops after a board is found but the link does not come up', async () => {
    // 500 means we dialled a real board and got no heartbeat. Retrying every
    // few seconds would re-dial a silent vehicle and bury the event log; this
    // one needs a human.
    const fetchMock = mockBackend([
      { status: 500, body: { detail: 'Found a flight controller but the link did not come up — COM3: no heartbeat' } },
    ]);
    global.fetch = fetchMock;

    render(<App />);
    await vi.advanceTimersByTimeAsync(0);
    expect(attempts(fetchMock)).toBe(1);

    await vi.advanceTimersByTimeAsync(20000);
    expect(attempts(fetchMock)).toBe(1);
  });

  test('does not stack overlapping probes', async () => {
    // Each attempt holds a busy flag until it settles, so a slow serial dial
    // cannot have a second probe fired on top of it.
    let release;
    const pending = new Promise((res) => { release = res; });
    const fetchMock = vi.fn((url) => {
      if (String(url).endsWith(AUTOCONNECT)) return pending;
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ connected: false, reconnecting: false }) });
    });
    global.fetch = fetchMock;

    render(<App />);
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(12000);

    expect(attempts(fetchMock)).toBe(1);
    release({ ok: false, status: 404, json: () => Promise.resolve({}) });
  });
});

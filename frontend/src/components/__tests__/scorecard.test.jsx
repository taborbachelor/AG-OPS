/**
 * Post-flight scorecard UI.
 *
 * The backend writes a scorecard on every disarm and serves it on
 * GET /api/logs/{name}, and until this component existed nothing rendered it
 * — a backend surface with no caller, the same seam class as the 86c6a6e
 * keepout-arming bug.
 *
 * These tests pin the two things that would make the panel actively unsafe
 * rather than merely incomplete:
 *
 *   1. A metric the flight never measured is null, and must render as a dash.
 *      Showing "0.0 m" for a nearest-powerline distance that was never taken
 *      is the exact dangerous lie the writer starts every extreme at None to
 *      avoid.
 *   2. A missing scorecard means NOT AVAILABLE, never "nothing to report".
 */
import React from 'react';
import { render, screen, act, fireEvent, within } from '@testing-library/react';
import { vi } from 'vitest';
import '@testing-library/jest-dom';
import Scorecard from '../Scorecard';
import LogsPanel from '../LogsPanel';

const CARD = {
  samples: 412,
  duration_s: 184.2,
  max_bank_deg: 61.4,
  max_ekf_pos_var: 0.55,
  max_ekf_vel_var: 0.21,
  max_vibe_ms2: 0.17,
  clip_events: 0,
  max_wind_ms: 12.0,
  min_airspeed_ms: 14.2,
  min_battery_v: 10.9,
  min_rtl_margin_s: 47.0,
  min_hazard_dist_m: 19.0,
  min_keepout_dist_m: 8.4,
  warnings: { bank: 3, ekf: 1 },
};

const rowFor = (label) => screen.getByText(label).closest('.sc-metric');

describe('Scorecard', () => {
  it('renders the four headline safety numbers', () => {
    render(<Scorecard card={CARD} />);
    expect(within(rowFor('Nearest hazard')).getByText('19.0')).toBeInTheDocument();
    expect(within(rowFor('Min RTL margin')).getByText('47')).toBeInTheDocument();
    expect(within(rowFor('Nearest keepout')).getByText('8.4')).toBeInTheDocument();
    // Max bank lives behind the extremes disclosure.
    fireEvent.click(screen.getByRole('button', { name: /flight extremes/i }));
    expect(within(rowFor('Max bank')).getByText('61.4')).toBeInTheDocument();
  });

  it('counts guardian warnings per monitor', () => {
    render(<Scorecard card={CARD} />);
    expect(within(rowFor('Bank angle')).getByText(/3/)).toBeInTheDocument();
    expect(within(rowFor('EKF')).getByText(/1/)).toBeInTheDocument();
    expect(screen.queryByText(/No monitor raised a warning/i)).not.toBeInTheDocument();
  });

  it('says so plainly when no monitor warned', () => {
    render(<Scorecard card={{ ...CARD, warnings: {} }} />);
    expect(screen.getByText(/No monitor raised a warning/i)).toBeInTheDocument();
  });

  it('renders an UNMEASURED metric as a dash, never as zero', () => {
    // No rings were ever loaded, so there is no nearest-hazard distance.
    render(<Scorecard card={{ ...CARD, min_hazard_dist_m: null }} />);
    const row = rowFor('Nearest hazard');
    expect(row).toHaveClass('sc-metric-missing');
    expect(within(row).getByText('\u2014')).toBeInTheDocument();
    expect(within(row).queryByText('0.0')).not.toBeInTheDocument();
    expect(within(row).queryByText('0')).not.toBeInTheDocument();
  });

  it('distinguishes an ABSENT scorecard from a clean flight', () => {
    render(<Scorecard card={null} />);
    expect(screen.getByText(/Not available for this flight/i)).toBeInTheDocument();
    expect(screen.getByText(/not a clean-flight result/i)).toBeInTheDocument();
    // Must not imply the flight was clean.
    expect(screen.queryByText(/No monitor raised a warning/i)).not.toBeInTheDocument();
  });
});

describe('LogsPanel wiring', () => {
  const LOGS = [
    { name: 'flight_20260819_101500.jsonl', started: '2026-08-19T10:15:00',
      duration: 184, samples: 736, size_kb: 88.1, has_scorecard: true },
  ];

  const mockFetch = (detail) => vi.fn(async (url) => {
    if (String(url).endsWith('/logs')) {
      return { ok: true, json: async () => ({ logs: LOGS }) };
    }
    return { ok: true, json: async () => detail };
  });

  const openFlight = async () => {
    const row = await screen.findByText(/2026-08-19 10:15:00/);
    await act(async () => { fireEvent.click(row); });
  };

  it('fetches the log and renders its scorecard', async () => {
    global.fetch = mockFetch({ name: LOGS[0].name, meta: {}, samples: [],
                               duration: 184, scorecard: CARD });
    render(<LogsPanel setPlaybackTelem={vi.fn()} setPlaybackPath={vi.fn()} />);
    await openFlight();
    expect(within(rowFor('Nearest hazard')).getByText('19.0')).toBeInTheDocument();
  });

  it('shows the absent state when the flight has no scorecard', async () => {
    global.fetch = mockFetch({ name: LOGS[0].name, meta: {}, samples: [],
                               duration: 184, scorecard: null });
    render(<LogsPanel setPlaybackTelem={vi.fn()} setPlaybackPath={vi.fn()} />);
    await openFlight();
    expect(screen.getByText(/Not available for this flight/i)).toBeInTheDocument();
  });

  it('marks which flights in the list have one', async () => {
    global.fetch = mockFetch({});
    render(<LogsPanel setPlaybackTelem={vi.fn()} setPlaybackPath={vi.fn()} />);
    expect(await screen.findByText('scorecard')).toBeInTheDocument();
  });
});

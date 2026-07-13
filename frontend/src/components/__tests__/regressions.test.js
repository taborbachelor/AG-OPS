/**
 * Regression tests for confirmed GCS audit findings:
 *  - SprayPanel: a plan response that resolves after the job changed must be
 *    dropped, and job-mutating buttons are disabled while a request runs.
 *  - FlightVitals: rejected in-flight commands (RTL/LAND/DISARM) must surface
 *    an error to the operator, never fail silently.
 *  - RCPanel: a dropped RC-override WebSocket must flip MANUAL CONTROL off
 *    and tell the operator — no false "LIVE" while sticks go nowhere.
 */
import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import '@testing-library/jest-dom';
import SprayPanel from '../SprayPanel';
import FlightVitals from '../FlightVitals';
import RCPanel from '../RCPanel';

const TRI = [
  { lat: 40.0, lon: -95.0 },
  { lat: 40.0, lon: -94.999 },
  { lat: 40.001, lon: -95.0 },
];

const sprayProps = (over = {}) => ({
  connected: true,
  draft: [], setDraft: jest.fn(),
  fields: [{ polygon: TRI, acres: null, source: 'drawn' }], setFields: jest.fn(),
  area: [], setArea: jest.fn(),
  drawing: false, setDrawing: jest.fn(),
  areaDrawing: false, setAreaDrawing: jest.fn(),
  snapping: false, setSnapping: jest.fn(),
  snapStatus: '',
  plan: null, setPlan: jest.fn(),
  zones: null, setZones: jest.fn(),
  homePos: null,
  ...over,
});

afterEach(() => {
  delete global.fetch;
});

describe('SprayPanel stale plan race', () => {
  test('drops a plan response that resolves after the job changed', async () => {
    let resolvePlan;
    global.fetch = jest.fn(() => new Promise((res) => { resolvePlan = res; }));

    const setPlan = jest.fn();
    render(<SprayPanel {...sprayProps({ setPlan, drawing: true, draft: TRI })} />);

    fireEvent.click(screen.getByText('Generate Spray Plan'));
    await screen.findByText('Working…');

    // Mutate the job while the request is in flight (commit the draft field).
    fireEvent.click(screen.getByText('Add field to job'));

    // Now the (stale) plan response arrives.
    await act(async () => {
      resolvePlan({
        ok: true,
        json: async () => ({
          combined_waypoints: [{ lat: 40, lon: -95, alt: 100 }],
          fields: [], flight_order: [], transits: [],
          totals: { fields: 1, area_acres: 1, est_time_s: 60, spray_path_m: 10, transit_m: 0, waypoints: 2 },
        }),
      });
    });

    await waitFor(() => expect(screen.getByText('Generate Spray Plan')).toBeInTheDocument());
    // setPlan may be called with null (invalidations) but never with the
    // stale plan payload.
    for (const call of setPlan.mock.calls) {
      expect(call[0]).toBeNull();
    }
  });

  test('Clear job and per-field remove are disabled while planning', async () => {
    global.fetch = jest.fn(() => new Promise(() => { /* never resolves */ }));

    render(<SprayPanel {...sprayProps()} />);
    fireEvent.click(screen.getByText('Generate Spray Plan'));
    await screen.findByText('Working…');

    expect(screen.getByText('Clear job')).toBeDisabled();
    expect(screen.getByTitle('Remove')).toBeDisabled();
  });
});

describe('FlightVitals command feedback', () => {
  const telem = {
    connected: true, armed: true, mode: 'AUTO',
    altitude: 120, airspeed: 18, groundspeed: 17, heading: 90,
    lat: 40.0, lon: -95.0, battery_voltage: 24.8, battery_current: 11.2,
    battery_level: 76, pitch: 0.02, roll: 0.01, yaw: 1.5,
    gps_fix: 3, gps_satellites: 12,
    rc_channels: [], rc_rssi: 0, servo_outputs: [],
    mission_seq: 2, mission_count: 10, wp_dist: 250,
    home_lat: 40.0, home_lon: -95.0,
  };

  test('a rejected RTL surfaces the backend error instead of failing silently', async () => {
    global.fetch = jest.fn(async () => ({
      ok: false,
      status: 400,
      json: async () => ({ detail: 'Not connected' }),
    }));

    render(<FlightVitals telemetry={telem} />);
    fireEvent.click(screen.getByText('RTL'));

    expect(await screen.findByText(/RTL FAILED — Not connected/)).toBeInTheDocument();
  });

  test('an unreachable backend surfaces a failure note for LAND', async () => {
    global.fetch = jest.fn(async () => { throw new TypeError('Failed to fetch'); });

    render(<FlightVitals telemetry={telem} />);
    fireEvent.click(screen.getByText('LAND'));

    expect(await screen.findByText(/LAND FAILED — no response from backend/)).toBeInTheDocument();
  });
});

describe('RCPanel manual-control link drop', () => {
  test('a closed RC override socket turns MANUAL CONTROL off and notes it', async () => {
    class FakeWS {
      constructor() {
        FakeWS.last = this;
        this.readyState = 0;
        this.onclose = null;
      }
      send() {}
      close() { this.readyState = 3; }
    }
    const realWS = global.WebSocket;
    global.WebSocket = FakeWS;
    const realGetGamepads = navigator.getGamepads;
    navigator.getGamepads = () => [];

    try {
      render(<RCPanel telemetry={{ rc_channels: [], rc_rssi: 0 }} connected={true} />);

      const toggle = screen.getByRole('checkbox');
      fireEvent.click(toggle);
      expect(toggle).toBeChecked();
      expect(FakeWS.last).toBeDefined();

      // Backend restart / socket drop: the server closes the WS.
      act(() => { FakeWS.last.onclose(); });

      expect(toggle).not.toBeChecked();
      expect(screen.getByText(/Manual control link lost/)).toBeInTheDocument();
    } finally {
      global.WebSocket = realWS;
      navigator.getGamepads = realGetGamepads;
    }
  });
});

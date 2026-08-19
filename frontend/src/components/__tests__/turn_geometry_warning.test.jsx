/**
 * Every plan carries stats.turn_bank_deg / turn_bank_ok / turn_radius_m /
 * turn_max_speed_ms per field (see coverage.py). On a field too narrow to
 * satisfy the bank limit, turn_bank_ok is FALSE — the operator must be told,
 * with turn_max_speed_ms as the actionable fix (seam S5, raised by bravo).
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import SprayPanel from '../SprayPanel';

const ZONES = { water: [], trees: [], buildings: [], powerline: [], holes: [] };

function planWithFields(fields) {
  return {
    combined_waypoints: [{ lat: 39.9, lon: -95.8, alt: 100 },
                         { lat: 39.901, lon: -95.8, alt: 100 }],
    combined_leg_kinds: ['spray'],
    fields,
    flight_order: fields.map((f) => ({ index: f.index, reversed: false })),
    transits: [],
    totals: { fields: fields.length, area_acres: 10, est_time_s: 60,
              spray_path_m: 100, transit_m: 0, waypoints: 2,
              keepout_overflights: 0 },
    zones: ZONES,
  };
}

function renderPanel(plan) {
  const props = {
    connected: true, plan, setPlan: () => {},
    zones: ZONES, setZones: () => {}, homePos: null,
    fields: [], setFields: () => {}, draft: [], setDraft: () => {},
    area: [], setArea: () => {},
    drawing: false, setDrawing: () => {},
    areaDrawing: false, setAreaDrawing: () => {},
    snapping: false, setSnapping: () => {}, snapStatus: null,
  };
  return render(<SprayPanel {...props} />);
}

describe('turn-geometry warning on the spray panel', () => {
  it('surfaces the field number, commanded bank, and fix speed when a field fails the bank limit', () => {
    const plan = planWithFields([
      { index: 0, waypoints: [], stats: {
        turn_bank_ok: false, turn_bank_deg: 52.3, turn_bank_limit_deg: 30,
        turn_radius_m: 40, turn_max_speed_ms: 12.4 } },
    ]);
    renderPanel(plan);
    expect(screen.getByText(/#1/)).toBeInTheDocument();
    expect(screen.getByText(/52\.3/)).toBeInTheDocument();
    expect(screen.getByText(/12\.4/)).toBeInTheDocument();
  });

  it('says nothing when every field meets the bank limit', () => {
    const plan = planWithFields([
      { index: 0, waypoints: [], stats: {
        turn_bank_ok: true, turn_bank_deg: 18, turn_bank_limit_deg: 30,
        turn_radius_m: 40, turn_max_speed_ms: null } },
    ]);
    renderPanel(plan);
    expect(screen.queryByText(/bank limit/i)).not.toBeInTheDocument();
  });
});

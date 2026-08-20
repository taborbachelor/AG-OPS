/**
 * Live guardian monitor readouts in the vitals drawer (seam S9b).
 *
 * The guardian judges nine monitors on every telemetry frame and ships the
 * whole verdict tree in `telemetry.guardian.monitors`. Nothing rendered it, so
 * a monitor was invisible until it had ALREADY tripped -- the operator saw the
 * breach banner, never the approach that led to it.
 *
 * The trap these tests exist for: `ok: true` IS NOT ALWAYS A PASS.
 *
 *   - No monitor judges anything while DISARMED. On the ground all nine report
 *     ok:true having evaluated nothing.
 *   - Bank and airspeed judge only while AIRBORNE, on the guardian's own gate.
 *   - `rtl_margin.ok` defaults to TRUE when the margin cannot be computed at
 *     all -- no pack capacity, no current draw, no home fix. A green return
 *     margin that was never estimated is the most dangerous cell on the panel.
 *   - `keepout.known` is reported separately from `keepout.ok` by the backend
 *     precisely so that "no rings loaded" cannot render as a green tick.
 *
 * M6 is also pinned here: this component holds no thresholds and computes no
 * verdicts. The HOME pill's colour used to come from a battery-percent rule
 * this file invented, which disagreed with the guardian by construction -- a
 * 76% pack 4 km downwind was green. It now comes from the guardian's energy
 * margin, and the paired tests below fail if it ever goes back.
 *
 * (Co-located rather than under __tests__/ on purpose: TASK-019/020 own that
 * directory's glob this wave. Same pattern as ConnectionOverlay.test.jsx.)
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { vi } from 'vitest';
import '@testing-library/jest-dom';
import FlightVitals, {
  OK, ALERT, UNKNOWN, monitorRows, homeMarginLevel,
} from './FlightVitals';

const MONITORS = {
  link: { ok: true, level: 'good' },
  gps: { ok: true, fix: 3, sats: 14 },
  battery: { ok: true, volts: 11.4, low_sustained: false },
  rtl_margin: { ok: true, time_left_s: 480.0, time_home_s: 92.0, margin_s: 298.0 },
  ekf: { ok: true, healthy: true, pos_var: 0.12, vel_var: 0.08 },
  vibration: { ok: true, peak_ms2: 18.0, new_clips: 0 },
  airspeed: { ok: true, airspeed: 21.5, airborne: true },
  bank: { ok: true, roll_deg: 12.0, limit_deg: 31.5, low_alt: true },
  keepout: {
    ok: true, known: true, hazard_dist_m: 410.0, hazard_kind: 'powerline',
    keepout_dist_m: 120.0, keepout_complete: true,
  },
};

const guardian = (over = {}) => ({
  state: 'NORMAL', warnings: [], warning_items: [],
  rtl_source: null, rtl_reason: null,
  monitors: { ...MONITORS, ...over },
});

const telem = (over = {}) => ({
  connected: true, armed: true, mode: 'AUTO',
  altitude: 20, airspeed: 21.5, groundspeed: 20, heading: 90,
  lat: 40.02, lon: -95.0, battery_voltage: 11.4, battery_current: 11.2,
  battery_level: 76, pitch: 0.02, roll: 0.2, yaw: 1.5,
  gps_fix: 3, gps_satellites: 14,
  rc_channels: [], rc_rssi: 0, servo_outputs: [],
  mission_seq: 2, mission_count: 10, wp_dist: 250,
  home_lat: 40.0, home_lon: -95.0,
  guardian: guardian(),
  ...over,
});

/** Open the drawer, where the monitor rows live. */
function openDrawer(over = {}) {
  const out = render(<FlightVitals telemetry={telem(over)} />);
  fireEvent.click(screen.getByTitle('Tap for details'));
  return out;
}

const rowFor = (key, g, armed = true) =>
  monitorRows(g, armed).find((r) => r.key === key);

describe('the approach to a limit is visible, not just the breach', () => {
  it('renders live values against the guardian OWN limits', () => {
    openDrawer();
    expect(screen.getByText('GUARDIAN MONITORS')).toBeInTheDocument();
    // Metres to the powerline, long before any breach.
    expect(screen.getByText(/410 m to powerline/)).toBeInTheDocument();
    // Energy margin, broken into the numbers that produced it.
    expect(screen.getByText(/298s spare — 480s pack vs 92s home/)).toBeInTheDocument();
    // Measured bank against the guardian's limit, not one of ours: 31.5 is the
    // low-altitude-tightened figure the backend computed.
    expect(screen.getByText(/12° of 31\.5°/)).toBeInTheDocument();
    expect(screen.getByText(/limit tightened: low altitude/)).toBeInTheDocument();
  });

  it('renders every monitor the guardian reports', () => {
    const rows = monitorRows(guardian(), true);
    expect(rows.map((r) => r.key).sort()).toEqual([
      'airspeed', 'bank', 'battery', 'ekf', 'gps', 'keepout', 'link',
      'rtl_margin', 'vibration',
    ]);
    rows.forEach((r) => expect(r.level).toBe(OK));
  });
});

describe('ok:true is not a pass unless the monitor actually judged', () => {
  it('an UNCOMPUTABLE rtl margin reads UNKNOWN, never a healthy green', () => {
    // Exactly what the backend emits with no pack capacity configured:
    // ok defaults true, and every number is null.
    const g = guardian({
      rtl_margin: { ok: true, time_left_s: null, time_home_s: null, margin_s: null },
    });
    const row = rowFor('rtl_margin', g);
    expect(row.level).toBe(UNKNOWN);
    expect(row.value).toMatch(/no estimate/);
    expect(row.note).toMatch(/pack capacity/);
  });

  it('DISARMED reads UNKNOWN across the board, though every monitor says ok', () => {
    // On the ground the guardian evaluates nothing, so all nine are vacuous.
    const rows = monitorRows(guardian(), false);
    rows.forEach((r) => {
      expect(r.level, `${r.key} must not read OK while disarmed`).not.toBe(OK);
      expect(r.level).toBe(UNKNOWN);
    });
  });

  it('a keepout with no rings loaded is UNKNOWN, not a tick', () => {
    const g = guardian({
      keepout: { ok: true, known: false, hazard_dist_m: null, hazard_kind: null,
                 keepout_dist_m: null, keepout_complete: true },
    });
    const row = rowFor('keepout', g);
    expect(row.level).toBe(UNKNOWN);
    expect(row.note).toMatch(/no rings loaded/);
    expect(row.note).toMatch(/not being judged/);
  });

  it('a truncated ring set qualifies its own distance', () => {
    const g = guardian({ keepout: { ...MONITORS.keepout, keepout_complete: false } });
    expect(rowFor('keepout', g).note).toMatch(/subset answer/);
  });

  it('bank and airspeed are not judged on the ground, on the guardian gate', () => {
    const g = guardian({
      airspeed: { ok: true, airspeed: 0.0, airborne: false },
      bank: { ok: true, roll_deg: 3.0, limit_deg: 45.0, low_alt: true },
    });
    expect(rowFor('bank', g).level).toBe(UNKNOWN);
    expect(rowFor('bank', g).note).toMatch(/on the ground/);
    expect(rowFor('airspeed', g).level).toBe(UNKNOWN);
  });

  it('no guardian tree at all is admitted, not drawn as healthy', () => {
    expect(monitorRows(undefined, true)[0].level).toBe(UNKNOWN);
    expect(monitorRows({}, true)[0].level).toBe(UNKNOWN);
    expect(monitorRows({ monitors: {} }, true)[0].value).toMatch(/no guardian verdicts/);
    openDrawer({ guardian: undefined });
    expect(screen.getByText(/no guardian verdicts/)).toBeInTheDocument();
  });

  it('an UNKNOWN row is labelled UNKNOWN on screen, not just coloured', () => {
    // Colour alone is not a statement, and it is the first thing lost to a
    // sunlit tablet screen.
    openDrawer({ armed: false });
    expect(screen.getAllByText(/UNKNOWN/).length).toBeGreaterThan(0);
  });

  it('a real breach still reads as an alert', () => {
    const g = guardian({
      keepout: { ...MONITORS.keepout, ok: false, hazard_dist_m: 8.0 },
    });
    expect(rowFor('keepout', g).level).toBe(ALERT);
  });
});

describe('M6: the HOME pill colour is the guardian verdict, not ours', () => {
  // The paired cases. Under the old battery-percent rule the first would be
  // green and the second red -- both backwards.
  it('a HEALTHY pack with a negative guardian margin is an alert', () => {
    const g = guardian({
      rtl_margin: { ok: false, time_left_s: 60.0, time_home_s: 240.0, margin_s: -270.0 },
    });
    expect(homeMarginLevel(g, true)).toBe(ALERT);
  });

  it('a LOW pack with a healthy guardian margin is not an alert', () => {
    // 8% pack, but home is 40 m away: the guardian says there is margin.
    const g = guardian({
      rtl_margin: { ok: true, time_left_s: 120.0, time_home_s: 4.0, margin_s: 26.0 },
    });
    expect(homeMarginLevel(g, true)).toBe(OK);
  });

  it('no estimate means NO colour -- never a default green', () => {
    const g = guardian({
      rtl_margin: { ok: true, time_left_s: null, time_home_s: null, margin_s: null },
    });
    expect(homeMarginLevel(g, true)).toBe(null);
    expect(homeMarginLevel(guardian(), false)).toBe(null);   // disarmed
    expect(homeMarginLevel(undefined, true)).toBe(null);
    expect(homeMarginLevel({}, true)).toBe(null);
  });

  it('the rendered pill uses that verdict', () => {
    openDrawer({
      guardian: guardian({
        rtl_margin: { ok: false, time_left_s: 60.0, time_home_s: 240.0, margin_s: -270.0 },
      }),
    });
    const label = screen.getByText(/HOME M/);
    const value = label.parentElement.querySelector('.vital-value');
    expect(value).toHaveStyle({ color: 'var(--accent-red)' });
  });
});

describe('the existing vitals surface still works', () => {
  it('keeps rendering with no guardian key present at all', () => {
    // regressions.test.jsx renders exactly this shape.
    const bare = telem();
    delete bare.guardian;
    render(<FlightVitals telemetry={bare} />);
    expect(screen.getByText('RTL')).toBeInTheDocument();
    expect(screen.getByText('LAND')).toBeInTheDocument();
  });
});

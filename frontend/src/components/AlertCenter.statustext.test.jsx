/**
 * The flight controller's own STATUSTEXT feed (seam S9b).
 *
 * ArduPilot talks: "PreArm: Radio failsafe on", "Polygon fence breached",
 * "EKF variance". Those are the vehicle reporting something no GCS-side
 * monitor can know -- including, in the fence case, the enforcement that is
 * the entire reason exclusion rings get pushed onboard. The backend parses
 * them, ring-buffers the last five and logs them; nothing displayed them.
 *
 * Two design decisions pinned here, because both are easy to "improve" back:
 *   - the feed NEVER speaks. ArduPilot's chatter would talk over the guardian
 *     callouts that matter.
 *   - the feed is NOT dismissible. It is a transcript, not an annunciator;
 *     the banners above clear themselves when their condition resolves,
 *     a message that was said stays said.
 *
 * And the honesty rule: with the link down these five are the last thing we
 * heard, not the current state, so the feed says so rather than presenting
 * stale text as live.
 *
 * (Co-located rather than under __tests__/ on purpose: TASK-019/020 own that
 * directory's glob this wave.)
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { vi } from 'vitest';
import '@testing-library/jest-dom';
import AlertCenter, { statusFeed } from './AlertCenter';

const MESSAGES = [
  { t: 1000.0, severity: 6, severity_name: 'INFO', text: 'EKF2 IMU0 is using GPS' },
  { t: 1001.0, severity: 4, severity_name: 'WARNING', text: 'PreArm: Radio failsafe on' },
  { t: 1002.0, severity: 2, severity_name: 'CRITICAL', text: 'Polygon fence breached' },
];

const telem = (over = {}) => ({
  armed: true, mode: 'AUTO', battery_level: 80, gps_fix: 3,
  guardian: { state: 'NORMAL', warnings: [], warning_items: [],
              rtl_source: null, rtl_reason: null },
  statustext: MESSAGES,
  ...over,
});

beforeEach(() => {
  // jsdom has no SpeechSynthesisUtterance, and speak() swallows the throw --
  // so without this stub the "never speaks" test below passes vacuously
  // against code that speaks every message. Stub it so speak() really lands.
  global.SpeechSynthesisUtterance = class {
    constructor(text) { this.text = text; }
  };
  window.speechSynthesis = { speak: vi.fn() };
  localStorage.setItem('gcs_voice', 'on');   // voice ON, so silence is a choice
});

const renderCenter = (over = {}, connected = true) =>
  render(<AlertCenter telemetry={telem(over)} connected={connected}
    reconnecting={false} />);

describe('the vehicle gets a voice on screen', () => {
  it('shows what the flight controller actually said', () => {
    renderCenter();
    expect(screen.getByText('VEHICLE MESSAGES')).toBeInTheDocument();
    expect(screen.getByText('Polygon fence breached')).toBeInTheDocument();
    expect(screen.getByText('PreArm: Radio failsafe on')).toBeInTheDocument();
    expect(screen.getByText('EKF2 IMU0 is using GPS')).toBeInTheDocument();
  });

  it('newest first, and carries the severity the vehicle sent', () => {
    const { items } = statusFeed(telem(), true);
    expect(items.map((m) => m.text)).toEqual([
      'Polygon fence breached',
      'PreArm: Radio failsafe on',
      'EKF2 IMU0 is using GPS',
    ]);
    expect(items.map((m) => m.sev)).toEqual(['red', 'amber', 'info']);
    expect(items[0].severity_name).toBe('CRITICAL');
  });

  it('classifies severity the way MAVLink defines it', () => {
    const sev = (n) => statusFeed({ statustext: [{ t: 1, severity: n,
      severity_name: 'X', text: 't' }] }, true).items[0].sev;
    // 0..3 EMERGENCY/ALERT/CRITICAL/ERROR are failures.
    [0, 1, 2, 3].forEach((n) => expect(sev(n)).toBe('red'));
    expect(sev(4)).toBe('amber');                       // WARNING
    [5, 6, 7].forEach((n) => expect(sev(n)).toBe('info'));  // NOTICE/INFO/DEBUG
  });

  it('stays out of the way when the vehicle has said nothing', () => {
    renderCenter({ statustext: [] });
    expect(screen.queryByText('VEHICLE MESSAGES')).not.toBeInTheDocument();
    renderCenter({ statustext: undefined });
    expect(screen.queryByText('VEHICLE MESSAGES')).not.toBeInTheDocument();
    expect(statusFeed({}, true).items).toEqual([]);
    expect(statusFeed(undefined, true).items).toEqual([]);
  });
});

describe('a frozen feed is not a live one', () => {
  it('marks the feed as last-heard when the link is down', () => {
    renderCenter({}, false);
    expect(screen.getByText(/LAST HEARD, LINK DOWN/)).toBeInTheDocument();
    expect(statusFeed(telem(), false).stale).toBe(true);
  });

  it('does not claim staleness while connected', () => {
    renderCenter({}, true);
    expect(screen.queryByText(/LAST HEARD/)).not.toBeInTheDocument();
    expect(statusFeed(telem(), true).stale).toBe(false);
  });

  it('an empty feed is not "stale" -- there is nothing to be stale', () => {
    expect(statusFeed({ statustext: [] }, false).stale).toBe(false);
  });
});

describe('a transcript, not an annunciator', () => {
  it('never speaks the vehicle chatter, even with voice ON', () => {
    // A CRITICAL fence breach is in the feed and voice is enabled. The
    // guardian owns callouts; ArduPilot's stream would talk over them.
    renderCenter();
    const spoken = window.speechSynthesis.speak.mock.calls
      .map((c) => String(c[0] && c[0].text));
    spoken.forEach((s) => {
      expect(s).not.toMatch(/Polygon fence breached/);
      expect(s).not.toMatch(/PreArm/);
      expect(s).not.toMatch(/EKF2/);
    });
  });

  it('the speech path is really wired, so the test above means something', () => {
    // Guards the guard: a guardian warning with voice ON must reach
    // speechSynthesis. If this fails, "never speaks" above is vacuous.
    renderCenter({
      guardian: {
        state: 'WARNING',
        warnings: ['bank 60 deg past 45'],
        warning_items: [{ monitor: 'bank', text: 'bank 60 deg past 45' }],
        rtl_source: null, rtl_reason: null,
      },
    });
    const spoken = window.speechSynthesis.speak.mock.calls
      .map((c) => String(c[0] && c[0].text));
    expect(spoken).toContain('Bank angle');
  });

  it('is not dismissible -- said is said', () => {
    renderCenter();
    const line = screen.getByText('Polygon fence breached').closest('.stf-line');
    expect(line).toBeTruthy();
    expect(line.querySelector('.alert-x')).toBeNull();
  });
});

describe('the annunciator above it is untouched', () => {
  it('still renders guardian warnings alongside the feed', () => {
    renderCenter({
      guardian: {
        state: 'WARNING',
        warnings: ['bank 60 deg past 45'],
        warning_items: [{ monitor: 'bank', text: 'bank 60 deg past 45' }],
        rtl_source: null, rtl_reason: null,
      },
    });
    expect(screen.getByText(/bank 60 deg past 45/)).toBeInTheDocument();
    expect(screen.getByText('Polygon fence breached')).toBeInTheDocument();
  });
});

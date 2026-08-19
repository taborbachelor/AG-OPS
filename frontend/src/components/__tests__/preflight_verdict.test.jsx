/**
 * One-verdict pre-flight (UI queue item 2).
 *
 * M6 moved go/no-go authority to the backend: `preflight.py` evaluates the
 * same checklist that /arm and /takeoff enforce, and this panel renders that
 * verdict. The point of these tests is that it renders it and nothing else.
 *
 * The regression they exist to prevent is specific and was real: the panel
 * used to fall back to two locally-invented checks (link + GPS) whenever the
 * server poll had not landed, so it could display a PASS the backend would
 * refuse. A UI that disagrees with the gate is worse than one that admits it
 * does not know.
 */
import React from 'react';
import { render, screen, act, fireEvent } from '@testing-library/react';
import { vi } from 'vitest';
import '@testing-library/jest-dom';
import LaunchControl from '../LaunchControl';

const TELEM = { armed: false, gps_fix: 6, gps_satellites: 12, mode: 'MANUAL', altitude: 0 };

const CHECKS = [
  { id: 'link', label: 'Link READY', ok: true, blocker: true, detail: 'state=READY' },
  { id: 'gps', label: 'GPS 3D fix', ok: true, blocker: true, detail: 'fix=6, sats=12' },
  { id: 'ekf', label: 'EKF healthy', ok: true, blocker: true, detail: 'flags=831' },
  { id: 'home', label: 'Home position known', ok: true, blocker: true, detail: '' },
  { id: 'battery', label: 'Battery', ok: true, blocker: false, detail: '12.4V' },
  { id: 'rc', label: 'RC input seen', ok: true, blocker: false, detail: '8 channels' },
  { id: 'fence', label: 'Geofence enabled', ok: true, blocker: false, detail: 'FENCE_ENABLE=1' },
  { id: 'sensors', label: 'Sensors healthy', ok: true, blocker: false, detail: '' },
];

const withFailures = (failedIds) => CHECKS.map(
  (c) => (failedIds.includes(c.id) ? { ...c, ok: false } : c));

const READY = { ready: true, failed_blockers: [], advisories_failing: [], checks: CHECKS };

const BLOCKED = {
  ready: false,
  failed_blockers: ['gps', 'ekf'],
  advisories_failing: ['fence'],
  checks: withFailures(['gps', 'ekf', 'fence']),
};

const READY_WITH_ADVISORY = {
  ready: true,
  failed_blockers: [],
  advisories_failing: ['fence'],
  checks: withFailures(['fence']),
};

async function renderWith(verdict) {
  global.fetch = vi.fn(async (url) => {
    if (String(url).includes('/safety/preflight')) {
      if (verdict === null) throw new Error('backend down');
      return { ok: true, json: async () => verdict };
    }
    return { ok: true, json: async () => ({}) };
  });
  await act(async () => { render(<LaunchControl telemetry={TELEM} connected />); });
}

const launchBtn = () => screen.getByRole('button', { name: /ARM & TAKEOFF/i });

describe('one-verdict pre-flight', () => {
  it('shows READY and enables launch when the server says ready', async () => {
    await renderWith(READY);
    expect(screen.getByText('READY')).toBeInTheDocument();
    expect(screen.getByText(/Every check passes/i)).toBeInTheDocument();
    expect(launchBtn()).toBeEnabled();
  });

  it('names the failing BLOCKERS in one sentence and refuses launch', async () => {
    await renderWith(BLOCKED);
    expect(screen.getByText('NOT READY')).toBeInTheDocument();
    expect(screen.getByText(/Cannot arm: GPS 3D fix, EKF healthy\./)).toBeInTheDocument();
    expect(launchBtn()).toBeDisabled();
  });

  it('names a failing ADVISORY without blocking on it', async () => {
    await renderWith(READY_WITH_ADVISORY);
    expect(screen.getByText('READY')).toBeInTheDocument();
    expect(screen.getByText(/Not passing: Geofence enabled\./)).toBeInTheDocument();
    expect(launchBtn()).toBeEnabled();
  });

  it('admits it does not know rather than inventing a pass', async () => {
    await renderWith(null);                      // gate unreachable
    expect(screen.getByText(/CHECKING/)).toBeInTheDocument();
    expect(screen.getByText(/readiness is unknown/i)).toBeInTheDocument();
    // The old client-side fallback would have passed here: link is connected
    // and gps_fix is 6, so both invented checks were satisfied.
    expect(screen.queryByText('READY')).not.toBeInTheDocument();
    expect(launchBtn()).toBeDisabled();
  });

  it('keeps OVERRIDE reachable while the detail is collapsed', async () => {
    await renderWith(BLOCKED);
    expect(screen.queryByText(/Link READY/)).not.toBeInTheDocument();   // collapsed
    const override = screen.getByRole('checkbox', { name: /OVERRIDE/i });
    await act(async () => { fireEvent.click(override); });
    expect(launchBtn()).toBeEnabled();
  });

  it('puts the per-check detail behind the disclosure, closed by default', async () => {
    await renderWith(BLOCKED);
    expect(screen.queryByText(/Link READY/)).not.toBeInTheDocument();
    const toggle = screen.getByRole('button', { name: /checks/i });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await act(async () => { fireEvent.click(toggle); });
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText(/Link READY/)).toBeInTheDocument();
    expect(screen.getByText(/GPS 3D fix \(fix=6, sats=12\)/)).toBeInTheDocument();
  });
});

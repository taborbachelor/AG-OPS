/**
 * AlertCenter after the M6 alert-threshold unification.
 *
 * The point of that change: the backend guardian is the ONLY source of
 * in-flight verdicts. These tests pin the three things that were actually
 * broken before it — a second concurrent warning being hidden, the UI
 * disagreeing with the guardian about the battery, and per-warning state
 * leaking once a warning cleared.
 */
import React from 'react';
import { render, screen, act } from '@testing-library/react';
import { vi } from 'vitest';
import '@testing-library/jest-dom';
import AlertCenter from '../AlertCenter';

const telem = (over = {}) => ({
  armed: true, mode: 'AUTO', battery_level: 80, gps_fix: 3,
  guardian: { state: 'NORMAL', warnings: [], warning_items: [],
              rtl_source: null, rtl_reason: null },
  ...over,
});

const guardianWith = (items, over = {}) => telem({
  guardian: {
    state: items.length ? 'WARNING' : 'NORMAL',
    warnings: items.map((i) => i.text),
    warning_items: items,
    rtl_source: null, rtl_reason: null, ...over,
  },
});

beforeEach(() => {
  window.speechSynthesis = { speak: vi.fn() };
  localStorage.setItem('gcs_voice', 'off');
});

describe('AlertCenter renders backend verdicts', () => {
  it('shows EVERY concurrent guardian warning, not just the first', () => {
    // The old code rendered warnings[0] only, so a powerline-proximity
    // warning could be invisible behind a bank warning.
    render(<AlertCenter
      telemetry={guardianWith([
        { monitor: 'bank', text: 'bank 60 deg past 45' },
        { monitor: 'keepout', text: 'powerline 8 m away — inside the 20 m clearance' },
      ])}
      connected={true} reconnecting={false} />);

    expect(screen.getByText(/bank 60 deg past 45/)).toBeInTheDocument();
    expect(screen.getByText(/powerline 8 m away/)).toBeInTheDocument();
  });

  it('does not invent its own in-flight battery or GPS verdict', () => {
    // Armed, flat pack, no GPS fix — but the guardian says nothing. The UI
    // must stay quiet rather than contradict it: the guardian judges battery
    // on voltage and GPS on fix AND sat count.
    render(<AlertCenter
      telemetry={telem({ battery_level: 5, gps_fix: 0 })}
      connected={true} reconnecting={false} />);

    expect(screen.queryByText(/BATTERY/)).not.toBeInTheDocument();
    expect(screen.queryByText(/GPS/)).not.toBeInTheDocument();
  });

  it('still gives a pre-arm pack advisory while disarmed', () => {
    // The guardian is silent by design while disarmed, so this is the one
    // battery hint the client legitimately owns.
    render(<AlertCenter
      telemetry={telem({ armed: false, battery_level: 12 })}
      connected={true} reconnecting={false} />);

    expect(screen.getByText(/PACK LOW — 12% \(pre-arm\)/)).toBeInTheDocument();
  });

  it('owns LINK LOST, because guardian verdicts freeze when telemetry stops', () => {
    const { rerender } = render(<AlertCenter
      telemetry={telem()} connected={true} reconnecting={false} />);
    rerender(<AlertCenter
      telemetry={telem()} connected={false} reconnecting={false} />);

    expect(screen.getByText('LINK LOST')).toBeInTheDocument();
  });

  it('surfaces a guardian RTL with the recorded reason', () => {
    render(<AlertCenter
      telemetry={guardianWith([], { rtl_source: 'battery',
                                    rtl_reason: 'battery 10.3V below RTL threshold' })}
      connected={true} reconnecting={false} />);

    expect(screen.getByText(/GUARDIAN RTL — battery 10.3V/)).toBeInTheDocument();
  });

  it('clears a warning banner once the monitor recovers', () => {
    const items = [{ monitor: 'airspeed', text: 'airspeed low (6.0 m/s) — stall risk' }];
    const { rerender } = render(<AlertCenter
      telemetry={guardianWith(items)} connected={true} reconnecting={false} />);
    expect(screen.getByText(/airspeed low/)).toBeInTheDocument();

    rerender(<AlertCenter
      telemetry={guardianWith([])} connected={true} reconnecting={false} />);
    expect(screen.queryByText(/airspeed low/)).not.toBeInTheDocument();
  });

  it('speaks again when a warning returns after clearing', () => {
    // Guardian rules only exist while active, so they never hit the reset
    // branch that other rules use — the cleanup has to be explicit or a
    // recurring warning goes silent after its first occurrence.
    localStorage.setItem('gcs_voice', 'on');
    const spy = vi.fn();
    window.speechSynthesis = { speak: spy };
    global.SpeechSynthesisUtterance = function (t) { this.text = t; };

    const items = [{ monitor: 'bank', text: 'bank 60 deg past 45' }];
    const { rerender } = render(<AlertCenter
      telemetry={guardianWith(items)} connected={true} reconnecting={false} />);
    const afterFirst = spy.mock.calls.length;
    expect(afterFirst).toBeGreaterThan(0);

    rerender(<AlertCenter telemetry={guardianWith([])}
      connected={true} reconnecting={false} />);
    rerender(<AlertCenter telemetry={guardianWith(items)}
      connected={true} reconnecting={false} />);

    expect(spy.mock.calls.length).toBeGreaterThan(afterFirst);
  });
});

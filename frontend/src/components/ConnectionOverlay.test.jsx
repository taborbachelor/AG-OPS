import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';
import '@testing-library/jest-dom';

import ConnectionOverlay from './ConnectionOverlay';

const CUBE = {
  device: 'COM3',
  description: 'CubeOrange (COM3)',
  vid: 0x2dae,
  board: 'Cube (Hex/ProfiCNC)',
  is_flight_controller: true,
  suggested_baud: 115200,
};

const SIK_RADIO = {
  device: 'COM5',
  description: 'USB Serial Port (COM5)',
  vid: 0x0403,
  board: null,
  is_flight_controller: false,
  suggested_baud: 57600,
};

// Route by URL so a test only has to say what /ports returns.
function mockBackend(ports, { connectOk = true } = {}) {
  return vi.fn((url, opts) => {
    if (String(url).endsWith('/connection/ports')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(ports) });
    }
    if (String(url).endsWith('/connection/connect')) {
      return Promise.resolve({
        ok: connectOk, status: connectOk ? 200 : 500,
        json: () => Promise.resolve({ detail: 'no heartbeat from vehicle' }),
      });
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
  });
}

function renderOverlay(props = {}) {
  const spies = {
    setConnected: vi.fn(),
    onClose: vi.fn(),
    onManualConnect: vi.fn(),
    onManualDisconnect: vi.fn(),
  };
  render(<ConnectionOverlay connected={false} {...spies} {...props} />);
  return spies;
}

describe('ConnectionOverlay', () => {
  afterEach(() => vi.restoreAllMocks());

  test('says the board was recognised, without making the operator pick a port', async () => {
    global.fetch = mockBackend([SIK_RADIO, CUBE]);
    renderOverlay();
    expect(await screen.findByText(
      /Cube \(Hex\/ProfiCNC\) found on COM3 — connecting automatically/))
      .toBeInTheDocument();
  });

  test('stops claiming to be connecting once auto-connect is disarmed', async () => {
    // After a deliberate DISCONNECT the board is still plugged in, but nothing
    // is dialling it. Saying "connecting automatically" would leave the
    // operator waiting for a link that is never coming.
    global.fetch = mockBackend([CUBE]);
    renderOverlay({ autoConnectArmed: false });

    expect(await screen.findByText(/found on COM3/)).toBeInTheDocument();
    expect(screen.queryByText(/connecting automatically/)).not.toBeInTheDocument();
  });

  test('does not claim a telemetry radio is a Cube', async () => {
    // The old UI labelled every port "CUBE USB · 115200" — the wrong baud for
    // the one link where baud actually matters.
    global.fetch = mockBackend([SIK_RADIO]);
    renderOverlay();
    expect(await screen.findByText(/SERIAL · 57600/)).toBeInTheDocument();
    expect(screen.queryByText(/found on COM5/)).not.toBeInTheDocument();
  });

  test('quick-connect dials each port at its own baud', async () => {
    const fetchMock = mockBackend([SIK_RADIO, CUBE]);
    global.fetch = fetchMock;
    renderOverlay();

    const cubeBtn = await screen.findByText(/CUBE \(HEX\/PROFICNC\) · USB · 115200/);
    await userEvent.click(cubeBtn);

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([url]) => String(url).endsWith('/connection/connect'));
      expect(JSON.parse(call[1].body)).toEqual({
        connection_string: 'COM3', baud: 115200,
      });
    });
  });

  test('a deliberate disconnect suspends auto-connect before dropping the link', async () => {
    global.fetch = mockBackend([CUBE]);
    const spies = renderOverlay({ connected: true });

    await userEvent.click(await screen.findByText('DISCONNECT'));

    // Must fire — otherwise App's next probe re-dials the board the operator
    // just released.
    expect(spies.onManualDisconnect).toHaveBeenCalled();
  });

  test('a manual connect re-arms auto-connect', async () => {
    global.fetch = mockBackend([CUBE]);
    const spies = renderOverlay();

    await userEvent.click(await screen.findByText(/CUBE \(HEX\/PROFICNC\) · USB · 115200/));

    await waitFor(() => expect(spies.onManualConnect).toHaveBeenCalled());
    expect(spies.setConnected).toHaveBeenCalledWith(true);
  });

  test('surfaces a stopped auto-connect with a retry', async () => {
    global.fetch = mockBackend([CUBE]);
    const spies = renderOverlay({
      autoConnectError: 'Found a flight controller but the link did not come up',
    });

    expect(await screen.findByText(/Auto-connect stopped/)).toBeInTheDocument();
    // While it is stopped, the "connecting automatically" line must not also
    // be showing — the two would contradict each other.
    expect(screen.queryByText(/connecting automatically/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByText('↻ Retry'));
    expect(spies.onManualConnect).toHaveBeenCalled();
  });
});

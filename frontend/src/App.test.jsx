import React from 'react';
import { render, screen } from '@testing-library/react';
import { vi } from 'vitest';
import '@testing-library/jest-dom';

// react-leaflet v5 is ESM-only and the video feed wants real media devices —
// neither is under test in this shell smoke test. vi.mock calls are hoisted
// above the imports (same semantics as jest.mock under CRA before).
vi.mock('./components/MapView', () => ({ default: () => null }));
vi.mock('./components/MapView3D', () => ({ default: () => null }));
vi.mock('./components/VideoFeed', () => ({ default: () => null }));

import App from './App';

test('renders the GCS shell with link state and arm status', async () => {
  global.fetch = vi.fn(() => Promise.reject(new Error('backend down')));
  render(<App />);

  expect(screen.getByText('GCS')).toBeInTheDocument();
  expect(screen.getByText('DISARMED')).toBeInTheDocument();
  // The status poll cannot reach a backend in jsdom — that must be surfaced.
  expect(await screen.findByText('NO BACKEND')).toBeInTheDocument();
});

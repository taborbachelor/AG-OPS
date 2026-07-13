import React from 'react';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import App from './App';

// react-leaflet v5 is ESM-only (CRA's jest can't transform it) and the video
// feed wants real media devices — neither is under test in this shell smoke
// test. jest.mock calls are hoisted above the imports.
jest.mock('./components/MapView', () => () => null);
jest.mock('./components/VideoFeed', () => () => null);

test('renders the GCS shell with link state and arm status', async () => {
  global.fetch = jest.fn(() => Promise.reject(new Error('backend down')));
  render(<App />);

  expect(screen.getByText('GCS')).toBeInTheDocument();
  expect(screen.getByText('DISARMED')).toBeInTheDocument();
  // The status poll cannot reach a backend in jsdom — that must be surfaced.
  expect(await screen.findByText('NO BACKEND')).toBeInTheDocument();
});

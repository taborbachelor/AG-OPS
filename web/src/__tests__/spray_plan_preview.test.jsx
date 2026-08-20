/**
 * The review-step preview container (TASK-017).
 *
 * Two things worth a test here. First, Cesium must stay LAZY: this is a
 * booking funnel, and eagerly shipping a multi-megabyte 3D runtime to every
 * visitor costs conversions that the 3D view exists to win. Second, the
 * altitude sent to the planner must be a real spray altitude — the 3D view
 * draws the path at whatever comes back, so the old 100 m placeholder would
 * render as a drone spraying from 100 m.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Leaflet and Cesium both want a real canvas/WebGL context, which jsdom has
// not got. Stub both renderers: this file is about the container's decisions,
// and the geometry that actually matters is covered in preview3d.test.js.
vi.mock('../PlanMap2D', () => ({
  default: ({ plan }) => (
    <div data-testid="map-2d">{plan ? 'plan-loaded' : 'no-plan'}</div>
  ),
}))

// vi.hoisted so the flag exists before the hoisted vi.mock factory runs. An
// EAGER import of FieldPreview3D would run that factory at module-load time
// and flip this to true before a single test body executes -- which is exactly
// what the "does NOT pull Cesium in" test below reads.
const cesium = vi.hoisted(() => ({ loaded: false }))
vi.mock('../FieldPreview3D', () => {
  cesium.loaded = true
  return { default: () => <div data-testid="map-3d" /> }
})

import SprayPlanPreview, { PREVIEW_SWATH_M } from '../SprayPlanPreview'

const FIELD = [
  { lat: 39.900, lon: -95.800 },
  { lat: 39.904, lon: -95.800 },
  { lat: 39.904, lon: -95.795 },
]

const PLAN = {
  waypoints: [
    { lat: 39.900, lon: -95.800, alt: 20 },
    { lat: 39.904, lon: -95.800, alt: 20 },
  ],
  stats: { n_passes: 6, est_time_s: 480 },
}

beforeEach(() => {
  global.fetch = vi.fn(() =>
    Promise.resolve({ ok: true, json: () => Promise.resolve(PLAN) }))
})

afterEach(() => {
  vi.restoreAllMocks()
})

const body = () => JSON.parse(global.fetch.mock.calls[0][1].body)

describe('the altitude it asks the planner for', () => {
  it('is a real spray altitude, not the old 100 m placeholder', () => {
    render(<SprayPlanPreview vertices={FIELD} />)
    // 100 was a placeholder the TASK-012 backend sweep never reached; the
    // decisions log named THIS file as the copy it missed.
    expect(body().alt).not.toBe(100)
  })

  it('sits in the band aerial application actually works in', () => {
    // Same 10-25 m band the backend pins for DEFAULT_SPRAY_ALT_M
    // (backend/tests/test_spray_altitude_default.py).
    render(<SprayPlanPreview vertices={FIELD} />)
    expect(body().alt).toBeGreaterThanOrEqual(10)
    expect(body().alt).toBeLessThanOrEqual(25)
  })

  it('still sends the documented swath', () => {
    render(<SprayPlanPreview vertices={FIELD} />)
    expect(body().swath).toBe(PREVIEW_SWATH_M)
  })
})

describe('view switching', () => {
  it('opens top-down, and does NOT pull Cesium in to do it', async () => {
    render(<SprayPlanPreview vertices={FIELD} />)
    expect(screen.getByTestId('map-2d')).toBeInTheDocument()
    expect(screen.queryByTestId('map-3d')).not.toBeInTheDocument()
    // The load-bearing assertion: a visitor who never opens 3D never pays for
    // Cesium's runtime. If this fails, the lazy() import was made eager.
    expect(cesium.loaded).toBe(false)
  })

  it('shows the 3D view once asked for it', async () => {
    render(<SprayPlanPreview vertices={FIELD} />)
    fireEvent.click(screen.getByRole('radio', { name: '3D' }))
    await waitFor(() => expect(screen.getByTestId('map-3d')).toBeInTheDocument())
    // ...and NOW it has loaded. Without this the flag above could be stuck
    // false forever and the laziness test would pass for the wrong reason.
    expect(cesium.loaded).toBe(true)
  })

  it('fetches the plan once, not once per view', async () => {
    render(<SprayPlanPreview vertices={FIELD} />)
    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(1))
    fireEvent.click(screen.getByRole('radio', { name: '3D' }))
    await waitFor(() => expect(screen.getByTestId('map-3d')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('radio', { name: 'Top-down' }))
    // Switching views must not re-plan a field the customer is already
    // looking at -- that is a duplicate API call and a visible flicker.
    expect(global.fetch).toHaveBeenCalledTimes(1)
  })
})

describe('the caption', () => {
  it('states the altitude the returned waypoints actually carry', async () => {
    render(<SprayPlanPreview vertices={FIELD} />)
    await waitFor(() =>
      expect(screen.getByText(/20 m above your field/)).toBeInTheDocument())
  })

  it('says nothing about altitude when the waypoints disagree', async () => {
    global.fetch = vi.fn(() => Promise.resolve({
      ok: true,
      json: () => Promise.resolve({
        waypoints: [
          { lat: 39.9, lon: -95.8, alt: 20 },
          { lat: 39.901, lon: -95.8, alt: 40 },
        ],
        stats: { n_passes: 2, est_time_s: 120 },
      }),
    }))
    render(<SprayPlanPreview vertices={FIELD} />)
    await waitFor(() => expect(screen.getByText(/2 passes/)).toBeInTheDocument())
    expect(screen.queryByText(/above your field/)).not.toBeInTheDocument()
  })

  it('degrades to a note that does not scare the customer off the booking', async () => {
    global.fetch = vi.fn(() => Promise.reject(new Error('offline')))
    render(<SprayPlanPreview vertices={FIELD} />)
    await waitFor(() =>
      expect(screen.getByText(/doesn.t affect your booking/i)).toBeInTheDocument())
  })
})

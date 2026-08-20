/**
 * Pure geometry behind the 3D field preview (TASK-017).
 *
 * The point of this file is the altitude rule. The operator GCS shipped a 3D
 * view that defaulted a missing waypoint altitude to 60 m and drew a climb the
 * aircraft does not fly; TASK-024 removed that guess and made the backend state
 * the number instead. The customer-facing view is the one with a commercial
 * incentive to flatter, so the "read it, never invent it, never scale it for
 * looks" rule is pinned here rather than left to reviewer memory.
 */
import { describe, expect, it } from 'vitest'

import {
  GROUND_ALT_M,
  cameraTarget,
  planAltitude,
  planPathPoints,
  ringLatLonFlat,
} from '../preview3d'

const FIELD = [
  { lat: 39.900, lon: -95.800 },
  { lat: 39.904, lon: -95.800 },
  { lat: 39.904, lon: -95.795 },
  { lat: 39.900, lon: -95.795 },
]

const planAt = (alt, n = 4) => ({
  waypoints: Array.from({ length: n }, (_, i) => ({
    lat: 39.900 + i * 0.001, lon: -95.800, alt,
  })),
})

describe('planPathPoints', () => {
  it('reads the altitude off each waypoint', () => {
    const { positions, altitudes, missing } = planPathPoints(planAt(20))
    expect(missing).toBe(false)
    expect(altitudes).toEqual([20, 20, 20, 20])
    expect(positions[0]).toEqual([39.900, -95.800, 20])
  })

  it('does not scale or exaggerate the altitude for visual effect', () => {
    // A 20 m path over a 400 m field is genuinely a low, flat line. Any
    // multiplier here would make the preview a nicer picture of a different
    // aircraft, which is the exact bug TASK-024 removed from the GCS.
    const { altitudes } = planPathPoints(planAt(20))
    for (const a of altitudes) expect(a).toBe(20)
  })

  it('reports a missing altitude instead of inventing one', () => {
    const plan = { waypoints: [{ lat: 39.9, lon: -95.8 }, { lat: 39.901, lon: -95.8 }] }
    const { missing, altitudes } = planPathPoints(plan)
    expect(missing).toBe(true)
    // Falls to the ground reference, where a missing number LOOKS missing.
    expect(altitudes).toEqual([GROUND_ALT_M, GROUND_ALT_M])
  })

  it('never substitutes the old 60 m guess', () => {
    const plan = { waypoints: [{ lat: 39.9, lon: -95.8 }] }
    expect(planPathPoints(plan).altitudes).not.toContain(60)
  })

  it('survives an absent or empty plan', () => {
    for (const p of [null, false, undefined, {}, { waypoints: [] }]) {
      expect(planPathPoints(p).positions).toEqual([])
    }
  })
})

describe('planAltitude', () => {
  it('states the altitude when every waypoint agrees', () => {
    expect(planAltitude(planAt(20))).toBe(20)
  })

  it('refuses to average waypoints that disagree', () => {
    // "Flying at 25 m" would be a sentence no waypoint supports.
    const plan = {
      waypoints: [
        { lat: 39.9, lon: -95.8, alt: 20 },
        { lat: 39.901, lon: -95.8, alt: 30 },
      ],
    }
    expect(planAltitude(plan)).toBeNull()
  })

  it('is null when any altitude is missing, so the caption stays silent', () => {
    const plan = {
      waypoints: [{ lat: 39.9, lon: -95.8, alt: 20 }, { lat: 39.901, lon: -95.8 }],
    }
    expect(planAltitude(plan)).toBeNull()
  })

  it('is null for no plan at all', () => {
    expect(planAltitude(null)).toBeNull()
    expect(planAltitude({ waypoints: [] })).toBeNull()
  })
})

describe('cameraTarget', () => {
  it('centres on the field', () => {
    const t = cameraTarget(FIELD)
    expect(t.lat).toBeCloseTo(39.902, 6)
    expect(t.lon).toBeCloseTo(-95.7975, 6)
  })

  it('backs off far enough to frame the whole field', () => {
    // The field's larger extent is its ~445 m north-south span; the camera has
    // to sit further out than that or the boundary leaves the canvas.
    const t = cameraTarget(FIELD)
    expect(t.range).toBeGreaterThan(445)
  })

  it('keeps a floor so a tiny field does not put the camera underground', () => {
    const tiny = [
      { lat: 39.9000, lon: -95.8000 },
      { lat: 39.9001, lon: -95.8000 },
      { lat: 39.9001, lon: -95.7999 },
    ]
    expect(cameraTarget(tiny).range).toBeGreaterThanOrEqual(220)
  })

  it('returns null with no vertices rather than NaN coordinates', () => {
    // Infinity/-Infinity bounds would otherwise reach Cesium as NaN and throw
    // inside the viewer, taking the review step down with it.
    expect(cameraTarget([])).toBeNull()
    expect(cameraTarget(null)).toBeNull()
  })
})

describe('ringLatLonFlat', () => {
  it('flattens in lat, lon order', () => {
    expect(ringLatLonFlat(FIELD.slice(0, 2))).toEqual([39.900, -95.800, 39.904, -95.800])
  })

  it('is empty for no vertices', () => {
    expect(ringLatLonFlat(null)).toEqual([])
  })
})

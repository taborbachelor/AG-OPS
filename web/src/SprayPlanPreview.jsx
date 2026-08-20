import { lazy, Suspense, useEffect, useState } from 'react'

import PlanMap2D from './PlanMap2D'
import { planAltitude } from './preview3d'

// Cesium is several megabytes of runtime. Loading it eagerly would tax every
// visitor to a booking funnel — including the ones who never open the 3D view
// — so it is fetched on first switch to 3D and never before.
const FieldPreview3D = lazy(() => import('./FieldPreview3D'))

// Preview assumptions sent to the planner. The spray drone covers ~40 m per
// pass; ops may still tune swath/altitude before flight, so the copy below
// is deliberately phrased as an "about" estimate.
export const PREVIEW_SWATH_M = 40

// Metres AGL, and it must stay a REAL spray altitude.
//
// This was 100 m — a placeholder that survived the TASK-012 sweep because that
// fix only touched the backend's request models, and the decisions log
// (2026-08-19, the default-twins rule) recorded this file as the copy the fix
// missed. It stayed harmless only because Leaflet ignores altitude. The 3D view
// does not: it draws the path at whatever height this says, so a placeholder
// here becomes a picture of a drone spraying from 100 m, which is not a thing
// anyone would buy. Twins of this number: DEFAULT_SPRAY_ALT_M in
// backend/app/coverage.py (20.0, authoritative) and the `alt` defaults on
// CoverageRequest / AutoCoverageRequest / MultiRequest, which read from it.
const PREVIEW_ALT_M = 20

/**
 * Read-only review of what the customer is buying: their field and the
 * serpentine spray path the drone will actually fly, from POST
 * /api/coverage/plan, viewable top-down or in 3D.
 *
 * The preview is strictly decorative — any fetch failure collapses to a
 * subtle "unavailable" note and never blocks the checkout flow.
 *
 * Props:
 *   vertices  [{lat, lon}] closed field boundary (>= 3 points)
 */
export default function SprayPlanPreview({ vertices }) {
  // null = still loading, false = failed, object = {waypoints, stats}
  const [plan, setPlan] = useState(null)
  const [view, setView] = useState('2d')

  // Ask the planner for the real spray path. Cancelled flag guards against
  // setState after unmount (and StrictMode's dev double-mount). Fetched HERE
  // rather than in either map so switching views does not re-request a plan
  // the customer is already looking at.
  useEffect(() => {
    let cancelled = false
    setPlan(null)
    fetch('/api/coverage/plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        polygon: vertices,
        swath: PREVIEW_SWATH_M,
        alt: PREVIEW_ALT_M,
      }),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`coverage API returned ${res.status}`)
        return res.json()
      })
      .then((data) => {
        if (!cancelled) setPlan(data)
      })
      .catch(() => {
        if (!cancelled) setPlan(false)
      })
    return () => {
      cancelled = true
    }
  }, [vertices])

  const stats = plan ? plan.stats : null
  const minutes = stats ? Math.max(1, Math.round(stats.est_time_s / 60)) : 0
  // Read back from the plan, not from PREVIEW_ALT_M: the caption then states
  // what the returned waypoints actually say, so if the server ever plans at a
  // different altitude the customer is told the truth rather than our request.
  const altM = planAltitude(plan)

  return (
    <div className="plan-preview">
      <div className="plan-view-toggle" role="radiogroup" aria-label="Preview view">
        {[['2d', 'Top-down'], ['3d', '3D']].map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="radio"
            aria-checked={view === id}
            className={`slot-btn${view === id ? ' slot-btn-active' : ''}`}
            onClick={() => setView(id)}
          >
            {label}
          </button>
        ))}
      </div>

      {view === '2d' ? (
        <PlanMap2D vertices={vertices} plan={plan} />
      ) : (
        <Suspense
          fallback={<div className="plan-map plan-map-loading">Loading 3D view…</div>}
        >
          <FieldPreview3D vertices={vertices} plan={plan} />
        </Suspense>
      )}

      {stats ? (
        <p className="plan-note">
          <strong>
            Your drone will fly {stats.n_passes} pass{stats.n_passes === 1 ? '' : 'es'}
          </strong>{' '}
          — about {minutes} minute{minutes === 1 ? '' : 's'} in the air
          {altM != null ? `, ${Math.round(altM)} m above your field` : ''}.
        </p>
      ) : plan === false ? (
        <p className="plan-note muted">
          Spray-path preview unavailable right now — this doesn&rsquo;t affect
          your booking.
        </p>
      ) : (
        <p className="plan-note muted">Planning your drone&rsquo;s route…</p>
      )}
    </div>
  )
}

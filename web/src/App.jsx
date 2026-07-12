import { useMemo, useState } from 'react'
import FieldMap from './FieldMap.jsx'
import SprayPlanPreview from './SprayPlanPreview.jsx'
import TrackOrder from './TrackOrder.jsx'
import {
  polygonAcres,
  estimateCents,
  formatUSD,
  tomorrowISO,
} from './geometry.js'

// The orders API lives on the same backend as the GCS. Fetched relative so
// the Vite dev proxy (see vite.config.js) forwards it same-origin — a direct
// cross-origin call from :3001 would fail the backend's CORS preflight. The
// router may not be registered yet — every fetch failure degrades to a
// friendly offline banner.
const API_BASE = '/api/orders'

const STEPS = ['Contact', 'Field', 'Schedule', 'Review']

function App() {
  // 'landing' | 0..3 (STEPS index) | 'confirmed' | 'track'
  const [step, setStep] = useState('landing')

  // Order id handed to the tracking view ('' = customer types their own).
  const [trackId, setTrackId] = useState('')

  // Step 1 — contact
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')

  // Step 2 — field boundary
  const [vertices, setVertices] = useState([])
  const [closed, setClosed] = useState(false)

  // Step 3 — schedule
  const [date, setDate] = useState('')
  const [slot, setSlot] = useState('AM')

  // Submission state
  const [submitting, setSubmitting] = useState(false)
  const [offline, setOffline] = useState(false)
  const [formError, setFormError] = useState('')
  const [order, setOrder] = useState(null)

  const acres = useMemo(() => polygonAcres(vertices), [vertices])
  const minDate = useMemo(() => tomorrowISO(), [])

  function go(next) {
    setFormError('')
    setStep(next)
  }

  // --- per-step validation before advancing -------------------------------

  function submitContact(e) {
    e.preventDefault()
    if (!name.trim()) return setFormError('Please tell us your name.')
    if (!email.includes('@')) return setFormError('Please enter a valid email address.')
    go(1)
  }

  function submitField() {
    if (!closed || vertices.length < 3) {
      return setFormError('Trace your field and close the boundary first.')
    }
    go(2)
  }

  function submitSchedule(e) {
    e.preventDefault()
    if (!date || date < minDate) {
      return setFormError('Pick a day — the earliest we can fly is tomorrow.')
    }
    go(3)
  }

  // --- order submission ----------------------------------------------------

  async function confirmAndPay() {
    setSubmitting(true)
    setOffline(false)
    try {
      const res = await fetch(`${API_BASE}/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          email,
          phone: phone || null,
          field: { polygon: vertices },
          date,
          slot,
        }),
      })
      if (!res.ok) throw new Error(`orders API returned ${res.status}`)
      let placed = await res.json()

      // Dev-mode simulated payment; a failure here still leaves a valid
      // pending_payment order, so show the confirmation either way.
      try {
        const payRes = await fetch(`${API_BASE}/${placed.id}/pay`, { method: 'POST' })
        if (payRes.ok) placed = await payRes.json()
      } catch {
        /* keep pending_payment status */
      }

      setOrder(placed)
      setStep('confirmed')
    } catch {
      // Orders router not registered yet (or backend unreachable) — degrade
      // gracefully instead of crashing the review screen.
      setOffline(true)
    } finally {
      setSubmitting(false)
    }
  }

  // --- views ---------------------------------------------------------------

  if (step === 'landing') {
    return (
      <Shell>
        <section className="hero">
          <p className="eyebrow">Serving northeast Kansas</p>
          <h1>Autonomous field spraying</h1>
          <p className="lede">
            A precision drone covers your acres so you don&rsquo;t burn a day in
            the cab. Draw your field on a map, pick a day, and we handle the rest.
          </p>
          <ul className="benefits">
            <li>
              <strong>Precision passes</strong> — GPS-guided application with
              tight overlap control, edge to edge.
            </li>
            <li>
              <strong>Zero soil compaction</strong> — nothing heavy touches your
              field, whatever the ground conditions.
            </li>
            <li>
              <strong>Book in two minutes</strong> — instant online quote, no
              phone tag, no waiting on a callback.
            </li>
          </ul>
          <div className="price-line">
            from <span className="price-big">$12/acre</span>
            <span className="price-note">$150 job minimum</span>
          </div>
          <button className="btn btn-primary btn-lg" onClick={() => go(0)}>
            Get an instant quote
          </button>
          <p className="landing-track">
            Already booked?{' '}
            <button
              type="button"
              className="link-btn"
              onClick={() => {
                setTrackId('')
                go('track')
              }}
            >
              Check my order
            </button>
          </p>
        </section>
      </Shell>
    )
  }

  if (step === 'track') {
    return (
      <Shell>
        <TrackOrder initialId={trackId} onBack={() => go('landing')} />
      </Shell>
    )
  }

  if (step === 'confirmed' && order) {
    return (
      <Shell>
        <section className="card confirm-card">
          <div className="confirm-badge" aria-hidden="true">
            ✓
          </div>
          <h2>You&rsquo;re booked{order.status === 'paid' ? ' and paid' : ''}!</h2>
          <p className="muted">
            {order.acres.toFixed(1)} acres · {order.date} ({order.slot}) ·{' '}
            {formatUSD(order.price_cents)}
          </p>
          <div className="order-id-box">
            <span className="order-id-label">Your order ID</span>
            <code className="order-id">{order.id}</code>
          </div>
          <p className="save-note">
            <strong>Save this ID</strong> — you&rsquo;ll need it to check on or
            change your order.
          </p>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => {
              setTrackId(order.id)
              go('track')
            }}
          >
            Track your order
          </button>
          <p className="muted">
            Status: <span className="status-chip">{order.status}</span>
            {order.status === 'pending_payment' &&
              ' — payment didn’t go through; we’ll follow up by email.'}
          </p>
          <p className="muted">
            We&rsquo;ll email <strong>{order.email}</strong> when your spray day
            is confirmed on the flight schedule.
          </p>
        </section>
      </Shell>
    )
  }

  return (
    <Shell>
      <ProgressBar current={step} />

      {formError && <div className="banner banner-warn">{formError}</div>}

      {step === 0 && (
        <form className="card" onSubmit={submitContact}>
          <h2>Who are we spraying for?</h2>
          <label className="field">
            <span>Full name</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Ada Grower"
              autoComplete="name"
            />
          </label>
          <label className="field">
            <span>Email</span>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@farm.com"
              autoComplete="email"
            />
          </label>
          <label className="field">
            <span>
              Phone <em className="optional">(optional)</em>
            </span>
            <input
              type="tel"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              placeholder="785-555-0100"
              autoComplete="tel"
            />
          </label>
          <div className="actions">
            <button type="button" className="btn btn-ghost" onClick={() => go('landing')}>
              Back
            </button>
            <button type="submit" className="btn btn-primary">
              Next: draw your field
            </button>
          </div>
        </form>
      )}

      {step === 1 && (
        <div className="card card-map">
          <h2>Trace your field</h2>
          <p className="muted">
            Click the map to drop corners around your field, then click the
            first point (or &ldquo;Close field&rdquo;) to finish.
          </p>
          <FieldMap
            vertices={vertices}
            closed={closed}
            onAddVertex={(v) => {
              setFormError('')
              setVertices((prev) => [...prev, v])
            }}
            onClose={() => setClosed(true)}
          />
          <div className="map-toolbar">
            <div className="acres-readout">
              {vertices.length >= 3 ? (
                <>
                  <strong>{acres.toFixed(1)}</strong> acres
                  {!closed && <span className="muted"> (so far)</span>}
                </>
              ) : (
                <span className="muted">
                  {vertices.length === 0
                    ? 'No corners placed yet'
                    : `${vertices.length} corner${vertices.length > 1 ? 's' : ''} placed`}
                </span>
              )}
            </div>
            <div className="map-buttons">
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => {
                  setVertices([])
                  setClosed(false)
                }}
                disabled={vertices.length === 0}
              >
                Clear
              </button>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setClosed(true)}
                disabled={closed || vertices.length < 3}
              >
                Close field
              </button>
            </div>
          </div>
          <div className="actions">
            <button type="button" className="btn btn-ghost" onClick={() => go(0)}>
              Back
            </button>
            <button type="button" className="btn btn-primary" onClick={submitField}>
              Next: pick a day
            </button>
          </div>
        </div>
      )}

      {step === 2 && (
        <form className="card" onSubmit={submitSchedule}>
          <h2>When should we fly?</h2>
          <label className="field">
            <span>Spray date</span>
            <input
              type="date"
              min={minDate}
              value={date}
              onChange={(e) => setDate(e.target.value)}
            />
          </label>
          <div className="field">
            <span>Time of day</span>
            <div className="slot-toggle" role="radiogroup" aria-label="Time slot">
              {['AM', 'PM'].map((s) => (
                <button
                  key={s}
                  type="button"
                  role="radio"
                  aria-checked={slot === s}
                  className={`slot-btn${slot === s ? ' slot-btn-active' : ''}`}
                  onClick={() => setSlot(s)}
                >
                  {s === 'AM' ? 'Morning (AM)' : 'Afternoon (PM)'}
                </button>
              ))}
            </div>
          </div>
          <div className="actions">
            <button type="button" className="btn btn-ghost" onClick={() => go(1)}>
              Back
            </button>
            <button type="submit" className="btn btn-primary">
              Next: review
            </button>
          </div>
        </form>
      )}

      {step === 3 && (
        <div className="card">
          <h2>Review your order</h2>
          {offline && (
            <div className="banner banner-offline">
              Our booking system is offline right now — nothing was charged.
              Please try again in a few minutes, or email us and we&rsquo;ll
              book you in by hand.
            </div>
          )}
          <SprayPlanPreview vertices={vertices} />
          <dl className="review">
            <div>
              <dt>Contact</dt>
              <dd>
                {name} · {email}
                {phone ? ` · ${phone}` : ''}
              </dd>
            </div>
            <div>
              <dt>Field</dt>
              <dd>
                {acres.toFixed(1)} acres ({vertices.length} corners)
              </dd>
            </div>
            <div>
              <dt>Schedule</dt>
              <dd>
                {date} · {slot === 'AM' ? 'Morning (AM)' : 'Afternoon (PM)'}
              </dd>
            </div>
            <div className="review-total">
              <dt>
                Estimated total <span className="estimate-tag">estimate</span>
              </dt>
              <dd>{formatUSD(estimateCents(acres))}</dd>
            </div>
          </dl>
          <p className="fine-print">
            $12.00/acre with a $150.00 minimum. The final price is measured
            from your field boundary on our servers and confirmed before your
            card is charged.
          </p>
          <div className="actions">
            <button type="button" className="btn btn-ghost" onClick={() => go(2)}>
              Back
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={confirmAndPay}
              disabled={submitting}
            >
              {submitting ? 'Booking…' : 'Confirm & Pay'}
            </button>
          </div>
        </div>
      )}
    </Shell>
  )
}

/** Page chrome shared by every view: header, centered column, footer. */
function Shell({ children }) {
  return (
    <div className="shell">
      <header className="site-header">
        <span className="logo-mark" aria-hidden="true">
          ✈
        </span>
        <span className="logo-word">PrairieSpray</span>
      </header>
      <main className="content">{children}</main>
      <footer className="site-footer">
        <span>PrairieSpray · Sabetha, Kansas · from $12/acre</span>
        {/* Placeholder contact details until the real lines are set up. */}
        <span className="footer-contact">
          Questions? hello@prairiespray.example · (785) 555-0148
        </span>
      </footer>
    </div>
  )
}

/** Four-dot step indicator for the booking flow. */
function ProgressBar({ current }) {
  return (
    <ol className="progress" aria-label="Booking progress">
      {STEPS.map((label, i) => (
        <li
          key={label}
          className={
            i === current ? 'prog-current' : i < current ? 'prog-done' : ''
          }
          aria-current={i === current ? 'step' : undefined}
        >
          <span className="prog-dot">{i < current ? '✓' : i + 1}</span>
          <span className="prog-label">{label}</span>
        </li>
      ))}
    </ol>
  )
}

export default App

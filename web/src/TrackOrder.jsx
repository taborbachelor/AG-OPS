import { useEffect, useRef, useState } from 'react'
import { formatUSD } from './geometry.js'

// Same proxied base as the booking flow in App.jsx — see the comment there
// for why this must stay a relative path.
const API_BASE = '/api/orders'

// Order lifecycle in display order; index comparisons drive the timeline.
// Mirrors _STATUS_CHAIN in backend/app/routers/orders.py.
const TIMELINE = [
  {
    key: 'pending_payment',
    label: 'Order placed',
    detail: 'Waiting on payment confirmation.',
  },
  { key: 'paid', label: 'Paid', detail: 'Payment received — you’re all set.' },
  {
    key: 'scheduled',
    label: 'Scheduled',
    detail: 'Your job is on the flight schedule.',
  },
  {
    key: 'in_progress',
    label: 'Spraying',
    detail: 'The drone is working your field.',
  },
  { key: 'done', label: 'Done', detail: 'All passes complete.' },
]

/**
 * "Check my order" view: order-id input -> status timeline + order summary.
 *
 * Props:
 *   initialId  optional order id to prefill and look up immediately (used by
 *              the post-payment confirmation's "track your order" pointer)
 *   onBack     () => void — return to the landing page
 */
export default function TrackOrder({ initialId, onBack }) {
  const [inputId, setInputId] = useState(initialId || '')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [order, setOrder] = useState(null)

  async function lookup(rawId) {
    const id = rawId.trim()
    if (!id) {
      setOrder(null)
      return setError(
        'Enter your order ID — it’s on your confirmation screen and in your email.',
      )
    }
    setLoading(true)
    setError('')
    setOrder(null)
    try {
      // encodeURIComponent keeps a pasted id with stray characters from
      // being read as extra path segments (which would hit other routes).
      const res = await fetch(`${API_BASE}/${encodeURIComponent(id)}`)
      if (res.status === 404) {
        return setError(
          'We couldn’t find an order with that ID. Double-check for typos — the ID is a long string of letters and numbers, no spaces.',
        )
      }
      if (!res.ok) throw new Error(`orders API returned ${res.status}`)
      setOrder(await res.json())
    } catch {
      setError(
        'Our booking system is unreachable right now — nothing is wrong with your order. Please try again in a few minutes.',
      )
    } finally {
      setLoading(false)
    }
  }

  // Arriving from the confirmation screen: look the order up straight away.
  // The ref makes the auto-lookup fire at most once per mount, even under
  // StrictMode's dev-only double effect run.
  const autoLooked = useRef(false)
  useEffect(() => {
    if (initialId && !autoLooked.current) {
      autoLooked.current = true
      lookup(initialId)
    }
  }, [initialId])

  function submit(e) {
    e.preventDefault()
    lookup(inputId)
  }

  const currentIdx = order ? TIMELINE.findIndex((s) => s.key === order.status) : -1

  return (
    <>
      <form className="card" onSubmit={submit}>
        <h2>Check my order</h2>
        <p className="muted track-intro">
          Paste the order ID from your confirmation screen or email and
          we&rsquo;ll show you where your spray job stands.
        </p>
        <div className="track-row">
          <input
            className="track-input"
            value={inputId}
            onChange={(e) => setInputId(e.target.value)}
            placeholder="e.g. 48b7e991aec64214…"
            aria-label="Order ID"
            spellCheck="false"
            autoComplete="off"
          />
          <button type="submit" className="btn btn-primary" disabled={loading}>
            {loading ? 'Looking up…' : 'Look up'}
          </button>
        </div>
        {error && <div className="banner banner-warn track-error">{error}</div>}
        <div className="actions">
          <button type="button" className="btn btn-ghost" onClick={onBack}>
            Back to home
          </button>
        </div>
      </form>

      {order && (
        <section className="card track-result" aria-label="Order status">
          <h2>Order status</h2>
          <ol className="timeline">
            {TIMELINE.map((stage, i) => {
              // 'done' is both the current stage and a finished one — check
              // it rather than leaving the last dot open forever.
              const isDone = i < currentIdx || order.status === 'done'
              const isCurrent = i === currentIdx
              return (
                <li
                  key={stage.key}
                  className={
                    (isDone ? 'tl-done' : '') + (isCurrent ? ' tl-current' : '')
                  }
                  aria-current={isCurrent ? 'step' : undefined}
                >
                  <span className="tl-dot" aria-hidden="true">
                    {isDone ? '✓' : i + 1}
                  </span>
                  <div className="tl-label">{stage.label}</div>
                  {isCurrent && <div className="tl-detail">{stage.detail}</div>}
                </li>
              )
            })}
          </ol>
          <dl className="review track-summary">
            <div>
              <dt>Name</dt>
              <dd>{order.name}</dd>
            </div>
            <div>
              <dt>Field</dt>
              <dd>{order.acres.toFixed(1)} acres</dd>
            </div>
            <div>
              <dt>Spray day</dt>
              <dd>
                {order.date} · {order.slot === 'AM' ? 'Morning (AM)' : 'Afternoon (PM)'}
              </dd>
            </div>
            <div className="review-total">
              <dt>Price</dt>
              <dd>{formatUSD(order.price_cents)}</dd>
            </div>
          </dl>
        </section>
      )}
    </>
  )
}

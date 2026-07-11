import React from 'react';

// Catches render-time errors anywhere in the tree so a single bad component
// can't white-screen the whole ground station mid-flight.
class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error('UI error:', error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{
          position: 'fixed', inset: 0, display: 'flex', flexDirection: 'column',
          alignItems: 'center', justifyContent: 'center', gap: 16,
          background: '#0a0e17', color: '#e0e8f0', fontFamily: 'Rajdhani, sans-serif',
          textAlign: 'center', padding: 24,
        }}>
          <div style={{ fontSize: 40 }}>⚠</div>
          <div style={{ fontSize: 18, color: '#ff9100' }}>The interface hit an error</div>
          <div style={{ fontSize: 13, color: 'rgba(160,180,200,0.7)', maxWidth: 480 }}>
            The flight controller link and logging are unaffected. Reload to recover the display.
          </div>
          <pre style={{
            fontSize: 11, color: '#ff6b6b', maxWidth: 600, overflow: 'auto',
            background: 'rgba(255,23,68,0.08)', padding: 10, borderRadius: 6,
          }}>{String(this.state.error)}</pre>
          <button
            onClick={() => window.location.reload()}
            style={{
              padding: '8px 20px', borderRadius: 6, border: '1px solid #00e5ff',
              background: 'rgba(0,229,255,0.15)', color: '#00e5ff', cursor: 'pointer',
              fontFamily: 'Orbitron, monospace', fontSize: 12, letterSpacing: 1,
            }}>
            RELOAD
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

export default ErrorBoundary;

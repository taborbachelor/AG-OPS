import React, { useState } from 'react';

function VideoFeed() {
  const [streamUrl, setStreamUrl] = useState('');
  const [active, setActive] = useState(false);

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '6px 12px', background: '#111827', borderBottom: '1px solid #1e293b',
      }}>
        <span style={{ fontSize: 11, fontWeight: 600, color: '#64748b', textTransform: 'uppercase', letterSpacing: 1 }}>
          FPV Feed
        </span>
        <input
          type="text"
          placeholder="Stream URL (rtsp:// or http://)"
          value={streamUrl}
          onChange={(e) => setStreamUrl(e.target.value)}
          style={{ flex: 1, fontSize: 11, padding: '4px 8px' }}
        />
        <button
          className="btn btn-primary"
          onClick={() => setActive(!active)}
          style={{ fontSize: 11, padding: '4px 10px' }}
        >
          {active ? 'Stop' : 'Start'}
        </button>
      </div>

      <div style={{
        flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: '#000', color: '#4b5563',
      }}>
        {active && streamUrl ? (
          <img
            src={streamUrl}
            alt="FPV Feed"
            style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }}
            onError={() => setActive(false)}
          />
        ) : (
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 36, marginBottom: 8 }}>📡</div>
            <div style={{ fontSize: 13 }}>No video feed</div>
            <div style={{ fontSize: 11, marginTop: 4 }}>Enter a stream URL to start</div>
          </div>
        )}
      </div>
    </div>
  );
}

export default VideoFeed;

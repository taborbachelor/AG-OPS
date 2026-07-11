import React, { useState } from 'react';

function VideoFeed() {
  const [streamUrl, setStreamUrl] = useState('');
  const [active, setActive] = useState(false);
  const [editing, setEditing] = useState(false);

  return (
    <div className="video-pip">
      <div className="video-pip-header">
        <span className="video-pip-label">FPV</span>
        {active && (
          <span className="video-pip-rec">
            <span className="rec-dot" />
            LIVE
          </span>
        )}
      </div>

      <div className="video-pip-content" onClick={() => setEditing(!editing)}>
        {active && streamUrl ? (
          <img
            src={streamUrl}
            alt="FPV"
            style={{ width: '100%', height: '100%', objectFit: 'contain' }}
            onError={() => setActive(false)}
          />
        ) : (
          <div style={{ textAlign: 'center' }}>
            {editing ? (
              <div style={{ padding: 12 }} onClick={(e) => e.stopPropagation()}>
                <input
                  type="text"
                  placeholder="rtsp:// or http:// stream URL"
                  value={streamUrl}
                  onChange={(e) => setStreamUrl(e.target.value)}
                  style={{
                    width: '90%', padding: '6px 10px', fontSize: 11,
                    background: 'rgba(0,10,30,0.8)',
                    border: '1px solid var(--glass-border)',
                    color: 'var(--text-primary)',
                    borderRadius: 4, outline: 'none',
                    fontFamily: 'var(--font-body)',
                  }}
                  autoFocus
                />
                <button
                  className="control-btn"
                  onClick={() => { setActive(true); setEditing(false); }}
                  style={{ marginTop: 8, fontSize: 11, padding: '4px 12px' }}
                >
                  START
                </button>
              </div>
            ) : (
              <>
                <div style={{ fontSize: 11, color: 'var(--text-dim)' }}>TAP TO CONFIGURE</div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default VideoFeed;

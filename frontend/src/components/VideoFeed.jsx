import React, { useState, useRef, useEffect } from 'react';

function VideoFeed() {
  const [source, setSource] = useState('device'); // 'device' | 'url'
  const [devices, setDevices] = useState([]);
  const [deviceId, setDeviceId] = useState('');
  const [streamUrl, setStreamUrl] = useState('');
  const [active, setActive] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [error, setError] = useState('');
  const videoRef = useRef(null);
  const streamRef = useRef(null);

  const stopStream = () => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (videoRef.current) videoRef.current.srcObject = null;
  };

  // Clean up the camera when unmounting.
  useEffect(() => () => stopStream(), []);

  const startDevice = async (id) => {
    setError('');
    stopStream();
    try {
      const constraints = { video: id ? { deviceId: { exact: id } } : true, audio: false };
      const stream = await navigator.mediaDevices.getUserMedia(constraints);
      streamRef.current = stream;
      if (videoRef.current) videoRef.current.srcObject = stream;
      setActive(true);
      // Labels are only populated after permission is granted.
      const devs = (await navigator.mediaDevices.enumerateDevices())
        .filter((d) => d.kind === 'videoinput');
      setDevices(devs);
      setDeviceId(id || (devs[0] && devs[0].deviceId) || '');
    } catch (e) {
      setActive(false);
      setError(
        e.name === 'NotAllowedError' ? 'Camera permission denied' :
        e.name === 'NotFoundError' ? 'No capture device found' :
        'Could not start capture device');
    }
  };

  const stop = () => { stopStream(); setActive(false); };

  const startUrl = () => {
    if (streamUrl) { setError(''); setActive(true); }
  };

  return (
    <div className={`video-pip ${expanded ? 'expanded' : ''}`}>
      <div className="video-pip-header">
        <span className="video-pip-label">FPV</span>
        <div className="video-src-tabs">
          <button className={source === 'device' ? 'on' : ''}
            onClick={() => { stop(); setError(''); setSource('device'); }}>CAM</button>
          <button className={source === 'url' ? 'on' : ''}
            onClick={() => { stop(); setError(''); setSource('url'); }}>URL</button>
        </div>
        {active && <span className="video-pip-rec"><span className="rec-dot" />LIVE</span>}
        <button className="video-expand" onClick={() => setExpanded((e) => !e)}
          title={expanded ? 'Shrink' : 'Expand'}>{expanded ? '⤡' : '⤢'}</button>
      </div>

      <div className="video-pip-content">
        {/* Device mode uses a <video>; URL mode an <img> (MJPEG/HTTP) */}
        {source === 'device' ? (
          <video ref={videoRef} autoPlay muted playsInline
            style={{ width: '100%', height: '100%', objectFit: 'contain',
                     display: active ? 'block' : 'none' }} />
        ) : (
          active && streamUrl && (
            <img src={streamUrl} alt="FPV" onError={() => setError('Stream failed to load')}
              style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
          )
        )}

        {!active && (
          <div className="video-placeholder">
            <div style={{ fontSize: 30, marginBottom: 6 }}>📡</div>
            {error ? <div style={{ color: 'var(--accent-red)', fontSize: 11 }}>{error}</div>
                   : <div style={{ fontSize: 11 }}>{source === 'device'
                       ? 'Connect a USB capture device' : 'Enter a stream URL'}</div>}
          </div>
        )}
      </div>

      <div className="video-controls">
        {source === 'device' ? (
          <>
            {devices.length > 1 && (
              <select value={deviceId} onChange={(e) => startDevice(e.target.value)}>
                {devices.map((d, i) => (
                  <option key={d.deviceId} value={d.deviceId}>
                    {d.label || `Camera ${i + 1}`}
                  </option>
                ))}
              </select>
            )}
            {!active
              ? <button className="control-btn success" onClick={() => startDevice(deviceId)}>Start</button>
              : <button className="control-btn danger" onClick={stop}>Stop</button>}
          </>
        ) : (
          <>
            <input type="text" placeholder="http://…/stream.mjpg" value={streamUrl}
              onChange={(e) => setStreamUrl(e.target.value)} />
            {!active
              ? <button className="control-btn success" onClick={startUrl}>Start</button>
              : <button className="control-btn danger" onClick={stop}>Stop</button>}
          </>
        )}
      </div>
    </div>
  );
}

export default VideoFeed;

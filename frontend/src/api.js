// Single source of truth for backend endpoint bases.
// Dev (npm start on :3000): backend runs separately on the same host at :8000.
// Packaged exe / production build: the page IS served by the backend — same origin.
const host =
  window.location.port === '3000'
    ? `${window.location.hostname}:8000`
    : window.location.host;

export const API = `${window.location.protocol}//${host}/api`;
export const WS_BASE = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${host}/api`;

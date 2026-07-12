import React from 'react';

// Left-edge navigation rail — one view active at a time, DJI/QGC style.
const ICONS = {
  fly: (
    <svg viewBox="0 0 24 24">
      <path d="M12 1 L13.2 9 L23 15 L23 17 L13.2 13.5 L13.2 20 L16 22 L16 23 L12 21.4 L8 23 L8 22 L10.8 20 L10.8 13.5 L1 17 L1 15 L10.8 9 Z" fill="currentColor" />
    </svg>
  ),
  plan: (
    <svg viewBox="0 0 24 24">
      <circle cx="5" cy="19" r="2.4" fill="currentColor" />
      <circle cx="19" cy="5" r="2.4" fill="currentColor" />
      <path d="M6.8 17.2 C10 14, 14 10, 17.2 6.8" stroke="currentColor" strokeWidth="1.6" fill="none" strokeDasharray="3 2.4" />
    </svg>
  ),
  spray: (
    <svg viewBox="0 0 24 24">
      <path d="M12 2 C12 2 5.5 9.8 5.5 14.6 A6.5 6.5 0 0 0 18.5 14.6 C18.5 9.8 12 2 12 2 Z" fill="currentColor" />
      <path d="M9 14.4 A3.4 3.4 0 0 0 11.6 17.8" stroke="#04121f" strokeWidth="1.4" fill="none" strokeLinecap="round" />
    </svg>
  ),
  safety: (
    <svg viewBox="0 0 24 24">
      <path d="M12 2 L20 5.5 V11 C20 16.6 16.6 20.7 12 22 C7.4 20.7 4 16.6 4 11 V5.5 Z" fill="currentColor" />
    </svg>
  ),
  rc: (
    <svg viewBox="0 0 24 24">
      <rect x="3" y="6" width="18" height="1.6" rx="0.8" fill="currentColor" />
      <circle cx="9" cy="6.8" r="2.2" fill="currentColor" />
      <rect x="3" y="11.2" width="18" height="1.6" rx="0.8" fill="currentColor" />
      <circle cx="15" cy="12" r="2.2" fill="currentColor" />
      <rect x="3" y="16.4" width="18" height="1.6" rx="0.8" fill="currentColor" />
      <circle cx="6.5" cy="17.2" r="2.2" fill="currentColor" />
    </svg>
  ),
  controls: (
    <svg viewBox="0 0 24 24">
      <rect x="2" y="10.4" width="11" height="3.2" rx="1" fill="currentColor" />
      <path d="M14 12 L22 7.2 V16.8 Z" fill="currentColor" />
    </svg>
  ),
  logs: (
    <svg viewBox="0 0 24 24">
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <path d="M10 8.2 L16 12 L10 15.8 Z" fill="currentColor" />
    </svg>
  ),
};

const VIEWS = [
  { id: 'fly', label: 'FLY' },
  { id: 'plan', label: 'PLAN' },
  { id: 'spray', label: 'SPRAY' },
  { id: 'safety', label: 'SAFETY' },
  { id: 'rc', label: 'RC' },
  { id: 'controls', label: 'CTRL' },
  { id: 'logs', label: 'LOGS' },
];

function NavRail({ view, setView, hidden }) {
  return (
    <nav className={`nav-rail ${hidden ? 'hidden' : ''}`}>
      {VIEWS.map((v) => (
        <button
          key={v.id}
          className={`nav-btn ${view === v.id ? 'active' : ''}`}
          onClick={() => setView(v.id)}
          title={v.label}
        >
          {ICONS[v.id]}
          <span>{v.label}</span>
        </button>
      ))}
    </nav>
  );
}

export default NavRail;

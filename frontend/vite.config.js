import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Vite replaces CRA (react-scripts) — the pattern proven in web/.
// Constraints that matter:
//  - dev port MUST stay 3000: src/api.js routes API calls to :8000 only when
//    the page is served from :3000 (packaged/prod is same-origin).
//  - build output MUST stay `build/`: backend/app/main.py serves it and the
//    PyInstaller spec bundles it from that path.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    strictPort: true,
  },
  build: {
    outDir: 'build',
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/setupTests.js'],
  },
});

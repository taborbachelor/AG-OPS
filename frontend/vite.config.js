import { createReadStream, cpSync, existsSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';

import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// CesiumJS needs its static runtime (Workers/Assets/Widgets/ThirdParty)
// served under a fixed base URL (/cesium). Copied verbatim into the build,
// streamed straight from node_modules in dev.
const CESIUM_SRC = resolve(import.meta.dirname, 'node_modules/cesium/Build/Cesium');
const CESIUM_DIRS = ['Workers', 'ThirdParty', 'Assets', 'Widgets'];
const MIME = {
  '.js': 'text/javascript', '.mjs': 'text/javascript', '.wasm': 'application/wasm',
  '.css': 'text/css', '.json': 'application/json', '.png': 'image/png',
  '.jpg': 'image/jpeg', '.svg': 'image/svg+xml', '.woff2': 'font/woff2',
  '.xml': 'application/xml', '.gz': 'application/gzip', '.ktx2': 'image/ktx2',
};

function cesiumAssets() {
  return {
    name: 'cesium-assets',
    configureServer(server) {
      server.middlewares.use('/cesium', (req, res, next) => {
        const rel = decodeURIComponent((req.url || '').split('?')[0]);
        const p = join(CESIUM_SRC, rel);
        if (!p.startsWith(CESIUM_SRC) || !existsSync(p) || !statSync(p).isFile()) {
          return next();
        }
        const ext = p.slice(p.lastIndexOf('.')).toLowerCase();
        res.setHeader('Content-Type', MIME[ext] || 'application/octet-stream');
        createReadStream(p).pipe(res);
      });
    },
    closeBundle() {
      for (const d of CESIUM_DIRS) {
        cpSync(join(CESIUM_SRC, d),
               resolve(import.meta.dirname, 'build/cesium', d),
               { recursive: true });
      }
    },
  };
}

// Vite replaces CRA (react-scripts) — the pattern proven in web/.
// Constraints that matter:
//  - dev port MUST stay 3000: src/api.js routes API calls to :8000 only when
//    the page is served from :3000 (packaged/prod is same-origin).
//  - build output MUST stay `build/`: backend/app/main.py serves it and the
//    PyInstaller spec bundles it from that path.
export default defineConfig({
  plugins: [react(), cesiumAssets()],
  define: {
    CESIUM_BASE_URL: JSON.stringify('/cesium'),
  },
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

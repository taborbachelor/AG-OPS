import { createReadStream, cpSync, existsSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// CesiumJS needs its static runtime (Workers/Assets/Widgets/ThirdParty) served
// under a fixed base URL (/cesium): streamed from node_modules in dev, copied
// verbatim into the build. Deliberately the SAME arrangement as the operator
// GCS (frontend/vite.config.js) — that one is proven, and two customer/operator
// copies of this that drift is a debugging trap nobody would enjoy.
//
// The customer site loads Cesium LAZILY (see SprayPlanPreview.jsx), so none of
// this reaches a visitor who never opens the 3D view.
const CESIUM_SRC = resolve(import.meta.dirname, 'node_modules/cesium/Build/Cesium')
const CESIUM_DIRS = ['Workers', 'ThirdParty', 'Assets', 'Widgets']
const MIME = {
  '.js': 'text/javascript', '.mjs': 'text/javascript', '.wasm': 'application/wasm',
  '.css': 'text/css', '.json': 'application/json', '.png': 'image/png',
  '.jpg': 'image/jpeg', '.svg': 'image/svg+xml', '.woff2': 'font/woff2',
  '.xml': 'application/xml', '.gz': 'application/gzip', '.ktx2': 'image/ktx2',
}

function cesiumAssets() {
  return {
    name: 'cesium-assets',
    configureServer(server) {
      server.middlewares.use('/cesium', (req, res, next) => {
        const rel = decodeURIComponent((req.url || '').split('?')[0])
        const p = join(CESIUM_SRC, rel)
        if (!p.startsWith(CESIUM_SRC) || !existsSync(p) || !statSync(p).isFile()) {
          return next()
        }
        const ext = p.slice(p.lastIndexOf('.')).toLowerCase()
        res.setHeader('Content-Type', MIME[ext] || 'application/octet-stream')
        createReadStream(p).pipe(res)
      })
    },
    closeBundle() {
      // web/ keeps vite's default outDir ('dist'); the operator frontend uses
      // 'build' because the backend serves it from there. Different on purpose.
      for (const d of CESIUM_DIRS) {
        cpSync(join(CESIUM_SRC, d),
               resolve(import.meta.dirname, 'dist/cesium', d),
               { recursive: true })
      }
    },
  }
}

// Customer-facing ordering site. Dev server runs on 3001 because the
// operator GCS frontend already owns 3000.
export default defineConfig({
  plugins: [react(), cesiumAssets()],
  define: {
    CESIUM_BASE_URL: JSON.stringify('/cesium'),
  },
  server: {
    port: 3001,
    // Forward API calls to the GCS backend so the browser sees them as
    // same-origin — the backend's CORS allowlist (which only names the
    // operator frontend on 3000) never comes into play.
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/setupTests.js'],
  },
})

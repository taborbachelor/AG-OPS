import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Customer-facing ordering site. Dev server runs on 3001 because the
// operator GCS frontend already owns 3000.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3001,
    // Forward API calls to the GCS backend so the browser sees them as
    // same-origin — the backend's CORS allowlist (which only names the
    // operator frontend on 3000) never comes into play.
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})

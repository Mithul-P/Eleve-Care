import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In dev, /api and /ws are proxied to the FastAPI backend (same paths nginx uses in Docker).
const target = process.env.VITE_PROXY_TARGET || 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, '') },
      '/ws': { target: target.replace(/^http/, 'ws'), ws: true },
    },
  },
  build: { chunkSizeWarningLimit: 1200 },
})

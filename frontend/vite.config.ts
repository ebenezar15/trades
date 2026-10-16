import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Backend (FastAPI) runs on :8000 and serves the built `dist/` in production.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
  preview: { host: true, port: 4173 },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 900,
  },
});

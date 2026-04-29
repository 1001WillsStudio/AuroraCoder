import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      // Chat streaming + conversation history → conversation server
      '/api/chat': {
        target: 'http://localhost:8081',
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            // Disable buffering for SSE streams
            proxyRes.headers['X-Accel-Buffering'] = 'no';
            proxyRes.headers['Cache-Control'] = 'no-cache';
          });
        },
      },
      '/api/conversations': {
        target: 'http://localhost:8081',
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            proxyRes.headers['X-Accel-Buffering'] = 'no';
            proxyRes.headers['Cache-Control'] = 'no-cache';
          });
        },
      },
      // Everything else (sessions, files, providers, workspace) → backend
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    }
  },
  build: {
    outDir: 'dist',
    sourcemap: true
  }
})

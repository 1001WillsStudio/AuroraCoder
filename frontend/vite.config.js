import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      // Chat, conversations, file-display, workspace → gateway
      '/api/chat': {
        target: 'http://localhost:8081',
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
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
      '/api/files': {
        target: 'http://localhost:8081',
        changeOrigin: true,
      },
      '/api/workspace': {
        target: 'http://localhost:8081',
        changeOrigin: true,
      },
      // Everything else (providers, health, workspace info) → backend
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

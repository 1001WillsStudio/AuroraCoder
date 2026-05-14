import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import http from 'node:http'

// Dedicated HTTP agent per proxy target — prevents SSE long-poll connections
// from starving short-lived REST requests sharing the same socket pool.
const gatewayAgent = new http.Agent({ keepAlive: true, maxSockets: 20 })
const backendAgent = new http.Agent({ keepAlive: true, maxSockets: 10 })

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      // Chat, conversations, file-display, workspace → gateway
      '/api/chat': {
        target: 'http://localhost:8081',
        changeOrigin: true,
        agent: gatewayAgent,
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
        agent: gatewayAgent,
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
        agent: gatewayAgent,
      },
      '/api/workspace': {
        target: 'http://localhost:8081',
        changeOrigin: true,
        agent: gatewayAgent,
      },
      // Everything else (providers, health, workspace info) → backend
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
        agent: backendAgent,
      },
    }
  },
  build: {
    outDir: 'dist',
    sourcemap: true
  }
})

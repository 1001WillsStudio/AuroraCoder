import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import http from 'node:http'

const frontendPort  = parseInt(process.env.VITE_PORT            || '3000')
const backendPort   = parseInt(process.env.VITE_BACKEND_PORT    || '8080')
const gatewayPort   = parseInt(process.env.VITE_GATEWAY_PORT    || '3000')

// Dedicated HTTP agent per proxy target — prevents SSE long-poll connections
// from starving short-lived REST requests sharing the same socket pool.
const gatewayAgent = new http.Agent({ keepAlive: true, maxSockets: 20 })
const backendAgent = new http.Agent({ keepAlive: true, maxSockets: 10 })

export default defineConfig({
  plugins: [react()],
  server: {
    port: frontendPort,
    proxy: {
      // Chat, conversations, file-display, workspace → gateway
      '/api/chat': {
        target: `http://localhost:${gatewayPort}`,
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
        target: `http://localhost:${gatewayPort}`,
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
        target: `http://localhost:${gatewayPort}`,
        changeOrigin: true,
        agent: gatewayAgent,
      },
      '/api/workspace': {
        target: `http://localhost:${gatewayPort}`,
        changeOrigin: true,
        agent: gatewayAgent,
      },
      // Settings, providers → gateway (owns these routes now)
      '/api/settings': {
        target: `http://localhost:${gatewayPort}`,
        changeOrigin: true,
        agent: gatewayAgent,
      },
      '/api/providers': {
        target: `http://localhost:${gatewayPort}`,
        changeOrigin: true,
        agent: gatewayAgent,
      },
      // Everything else → backend
      '/api': {
        target: `http://localhost:${backendPort}`,
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

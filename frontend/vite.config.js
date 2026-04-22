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
      },
      '/api/conversations': {
        target: 'http://localhost:8081',
        changeOrigin: true,
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

import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Vite 配置文档：https://vite.dev/config/
const devProxyTarget = process.env.VITE_DEV_PROXY_TARGET?.trim() || 'http://127.0.0.1:8080'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
  },
  server: {
    port: 5173,
    proxy: {
      '/v1': {
        target: devProxyTarget,
        changeOrigin: true,
      },
      '/healthz': {
        target: devProxyTarget,
        changeOrigin: true,
      },
    },
  },
})

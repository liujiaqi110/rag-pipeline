/// <reference types="vitest" />
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

const api = process.env.KB_API_ORIGIN || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [vue()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: { port: 5173, proxy: { '/api': { target: api, changeOrigin: false } } },
  build: { outDir: 'dist', chunkSizeWarningLimit: 1600, sourcemap: false },
  test: { environment: 'jsdom', include: ['tests/unit/**/*.spec.ts'], css: false },
})

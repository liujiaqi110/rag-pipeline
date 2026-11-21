import { defineConfig } from '@playwright/test'
import { randomBytes } from 'node:crypto'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
const __dirname = path.dirname(fileURLToPath(import.meta.url))

// One random password per run, shared with the server process and the tests through the environment only.
process.env.KB_E2E_ADMIN_PASSWORD ||= 'E2e-' + randomBytes(12).toString('hex') + '9a'
process.env.KB_E2E_USER_PASSWORD ||= 'User-' + randomBytes(10).toString('hex') + '7b'
const port = process.env.KB_E2E_PORT || '8765'
const python = process.env.KB_E2E_PYTHON || path.resolve(__dirname, '..', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')

export default defineConfig({
  testDir: './e2e',
  timeout: 15 * 60_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    channel: process.env.KB_E2E_CHANNEL || 'msedge',   // installed Microsoft Edge (no browser download)
    headless: process.env.KB_E2E_HEADED ? false : true,
    viewport: { width: 1440, height: 900 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    locale: 'zh-CN',
  },
  webServer: {
    command: `"${python}" e2e/server.py`,
    url: `http://127.0.0.1:${port}/api/v1/health`,
    timeout: 180_000,
    reuseExistingServer: false,
    stdout: 'pipe',
    env: { ...process.env, KB_E2E_PORT: port, PYTHONIOENCODING: 'utf-8' } as Record<string, string>,
  },
})

import { defineConfig, devices } from '@playwright/test'
import { resolve } from 'node:path'

process.env.CONNECTORFORGE_HOME ??= resolve(`../.connectorforge/e2e-${process.pid}`)
process.env.CONNECTORFORGE_PORT = '8123'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: { baseURL: 'http://127.0.0.1:8123', trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'node e2e/serve.mjs',
    url: 'http://127.0.0.1:8123/healthz',
    timeout: 60_000,
    reuseExistingServer: false,
  },
})

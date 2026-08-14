import { defineConfig, devices } from '@playwright/test'

const pythonCommand = process.env.E2E_PYTHON || '.venv/bin/python'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    ...devices['Desktop Chrome'],
    baseURL: 'http://127.0.0.1:15173',
    channel: 'chrome',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure'
  },
  webServer: [
    {
      command: `cd .. && exec ${pythonCommand} -m uvicorn tests.e2e_server:app --host 127.0.0.1 --port 18080`,
      url: 'http://127.0.0.1:18080/api/health',
      reuseExistingServer: false,
      gracefulShutdown: { signal: 'SIGTERM', timeout: 5_000 },
      timeout: 60_000
    },
    {
      command: 'VITE_API_BASE_URL=http://127.0.0.1:18080 VITE_AUTH_ENABLED=true VITE_USE_MOCK=false npm run dev -- --host 127.0.0.1 --port 15173',
      url: 'http://127.0.0.1:15173',
      reuseExistingServer: false,
      timeout: 60_000
    }
  ]
})

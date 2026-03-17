import { defineConfig, devices } from '@playwright/test';
import dotenv from 'dotenv';
import path from 'path';

dotenv.config({ path: path.resolve(__dirname, '.env') });

let runServerCmd = "uv run invoke runserver --port 8000"

// npx playwright test
export default defineConfig({
  testDir: './playwright/tests',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  timeout: 120 * 1000, // 2 minutes
  workers: 1,
  reporter: [['html', { open: 'never' }], ['json', { outputFile: 'playwright-results.json' }]],
  use: {
    baseURL: 'http://localhost:8000',
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'setup',
      testMatch: /auth\.setup\.ts/,
    },
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        storageState: 'playwright/.auth/user.json',
      },
      dependencies: ['setup'],
      testIgnore: /01-authentication\.spec\.ts/,
    },
    {
      name: 'chromium-auth',
      use: { ...devices['Desktop Chrome'] },
      testMatch: /01-authentication\.spec\.ts/,
    },
  ],
  webServer: [
    {
      command: runServerCmd,
      url: 'http://localhost:8000',
      reuseExistingServer: process.env.REUSE_SERVER === '1' || !process.env.CI,
      env: {
        USE_DEBUG_TOOLBAR: "False",
        SECRET_KEY: process.env.SECRET_KEY || 'secret-test-key',
      },
    },
    {
      command: 'uv run inv celery',
      reuseExistingServer: process.env.REUSE_SERVER === '1' || !process.env.CI,
      env: {
        USE_DEBUG_TOOLBAR: "False",
        SECRET_KEY: process.env.SECRET_KEY || 'secret-test-key',
      },
    },
  ]
});

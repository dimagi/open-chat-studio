import { defineConfig, devices } from '@playwright/test';

let accountSetupCmd = "uv run python manage.py bootstrap_data --email tester@playwright.com --password My0riginalP@ssw0rd! --team-slug agent --team-name Agent --reset"
let runServerCmd = "uv run invoke runserver --port 8000"

// npx playwright test
export default defineConfig({
  testDir: './playwright/tests',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  timeout: 120 * 1000, // 2 minutes
  workers: 1,
  reporter: 'html',
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
      command: accountSetupCmd + " && " + runServerCmd,
      url: 'http://localhost:8000',
      reuseExistingServer: !process.env.CI,
      env: {
        USE_DEBUG_TOOLBAR: "0",
        SECRET_KEY: 'LTwzPMJVLeRNOjoLxqHidKWhfoOtjzYawyaGCezb',
      },
    },
    {
      command: 'uv run inv celery',
      reuseExistingServer: !process.env.CI,
      env: {
        USE_DEBUG_TOOLBAR: 'false',
        SECRET_KEY: 'LTwzPMJVLeRNOjoLxqHidKWhfoOtjzYawyaGCezb',
      },
    },
  ]
});

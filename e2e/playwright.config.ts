import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

import { E2E_BASE_URL, E2E_SERVER_PORT, E2E_START_SERVER } from "./config";

const repoRoot = path.resolve(__dirname, "..");

export default defineConfig({
  testDir: "tests",
  globalSetup: "./global-setup.ts",
  // The core flow waits on Celery tasks and live LLM calls.
  timeout: 3 * 60 * 1000,
  expect: { timeout: 60 * 1000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: E2E_BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: E2E_START_SERVER
    ? [
        {
          name: "django",
          command: `uv run python manage.py runserver 127.0.0.1:${E2E_SERVER_PORT} --noreload`,
          cwd: repoRoot,
          url: `${E2E_BASE_URL}/accounts/login/`,
          // The toolbar overlays page buttons and blocks clicks.
          env: { USE_DEBUG_TOOLBAR: "False" },
          stderr: "ignore",
          timeout: 2 * 60 * 1000,
        },
        {
          name: "celery",
          command: "uv run celery -A config worker -l INFO --pool=solo",
          cwd: repoRoot,
          wait: { stderr: / ready\.$/m },
          stderr: "ignore",
          timeout: 2 * 60 * 1000,
        },
      ]
    : undefined,
});

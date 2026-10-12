/** Settings shared by the Playwright config, global setup and tests. Each can be overridden with an env var. */

// Without E2E_BASE_URL, Playwright starts its own Django server and Celery worker on this port.
export const E2E_SERVER_PORT = 8010;
export const E2E_START_SERVER = !process.env.E2E_BASE_URL;
export const E2E_BASE_URL = process.env.E2E_BASE_URL || `http://127.0.0.1:${E2E_SERVER_PORT}`;

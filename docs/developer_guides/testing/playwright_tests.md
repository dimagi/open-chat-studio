# Playwright Tests

The end-to-end tests in `e2e/` drive the app in a browser to check the [core user stories](../../core_user_stories.md).

## Linking tests to user stories

Each test lists the stories it covers in a `user-stories` annotation, e.g. `{ type: "user-stories", description: "1, 2, 3" }`. The annotation shows in the Playwright HTML report.

`pnpm e2e:check-stories` (`e2e/check-user-stories.mts`) asks Playwright to list the tests with their annotations, without running them, and checks them against the stories doc. It fails when:

- a story has no "Covered by" entry, or lists a spec file that does not exist
- a listed spec has no test annotated with the story's number, or only skipped ones
- an annotation is malformed, or names a story that is not in the doc
- a story number appears twice in the doc, or Playwright cannot load the tests

CI runs it in the JavaScript job.

## Running the tests

Postgres and Redis must be running (`uv run inv up`), and the frontend assets must be built (`pnpm dev`).

```bash
pnpm install
pnpm exec playwright install chromium
pnpm e2e            # headless
pnpm e2e --headed   # watch it run in a browser window, in slow motion
pnpm e2e --ui       # Playwright UI: pick the test and press play
```

Playwright starts its own Django server on port 8010 and a Celery worker, then stops them when it exits. Both are needed: chat replies and chatbot version creation are Celery tasks. To run against an app you already started, set `E2E_BASE_URL` (e.g. `http://localhost:8000`) and Playwright will not start any servers. That app needs a Celery worker, `DEBUG` on or `CI=true` (for the mock LLM), and `USE_DEBUG_TOOLBAR=False`, because the toolbar covers buttons the test clicks.

Before the tests run, the global setup calls `uv run python manage.py setup_e2e_team`. That command deletes and recreates the `e2e-core` team, with `e2e@example.com` as its only member (Super Admin), and deletes the teams `e2e@example.com` created in earlier runs. No other team is changed. Because it deletes data, it refuses to run unless `DEBUG` is on or `CI=true`. The test logs in to `e2e-core`, creates a new team through the UI, and runs the remaining stories in that team. Sign-up is not tested: this command creates the user.

The test adds an OpenAI provider whose API base URL points at the mock LLM that the dev server serves under `/mock-llm/` (only when `DEBUG` is on or `CI=true`, which GitHub Actions sets). No API key is needed and no request leaves the machine; the mock answers with lorem ipsum.

| Variable         | Default                 | Purpose                                 |
|------------------|-------------------------|-----------------------------------------|
| `E2E_BASE_URL`   | (unset)                 | Use an already running app at this URL  |
| `E2E_EMAIL`      | `e2e@example.com`       | Login email for the test user           |
| `E2E_PASSWORD`   | `e2epassword`           | Login password for the test user        |
| `E2E_TEAM_SLUG`  | `e2e-core`              | Slug of the team the setup recreates    |
| `E2E_SLOW_MO`    | `500` headed, `0` else  | Delay in ms between browser actions     |

On failure, Playwright keeps a trace and screenshot in `test-results/`. Open the report with `pnpm exec playwright show-report`.

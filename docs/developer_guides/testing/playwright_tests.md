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

Each test starts from its own scenario: a user who owns a team, plus the data the test needs. The scenarios are listed in `e2e/scenarios.ts`, each with the state it starts from:

| State          | Creates                                                                 |
|----------------|-------------------------------------------------------------------------|
| `team`         | A user (`<random>@e2e.test`) who owns a new team                        |
| `llm_provider` | `team`, plus an OpenAI provider that points at the mock LLM             |
| `chatbot`      | `llm_provider`, plus a chatbot whose pipeline uses that provider        |
| `session`      | `chatbot`, plus a web session from the user with one message and reply  |

Before any test runs, the global setup (`e2e/global-setup.ts`) runs `manage.py e2e_seed` once, passing every scenario. The command first deletes the previous run's data: every `@e2e.test` user and every team whose members are all `@e2e.test` users. No other team or user is changed. It then uses the factories in `apps/utils/factories/` (see `apps/web/management/commands/e2e_data_service.py`) to create each scenario, and prints the details as JSON. The global setup stores that JSON in the `E2E_SCENARIOS` env var, which the test workers inherit.

A test gets its data with the `scenario` fixture in `e2e/fixtures.ts`, e.g. `await scenario("createChatbot")`. The fixture also adds the scenario user's logged-in session cookie to the browser; the login test passes `{ logIn: false }` to fill in the login form instead. No two tests share a scenario, so they run in any order, and any single test can run on its own. To add a test, add its scenario to `e2e/scenarios.ts`.

The data stays in the database after the run, so you can log in as a scenario's user to look at what a failed test left behind. The next run deletes it. Re-running a test in `--ui` mode reuses the same scenario, including whatever the previous attempt changed.

The command runs on the machine running Playwright, with that machine's settings. With `E2E_BASE_URL`, that machine must use the same database as the app under test. The command refuses to run unless `DEBUG` is on or `CI=true`, because it creates users with a known password. Sign-up is not tested.

The providers the tests use point at the mock LLM that the dev server serves under `/mock-llm/` (only when `DEBUG` is on or `CI=true`, which GitHub Actions sets). No API key is needed and no request leaves the machine; the mock answers with lorem ipsum.

| Variable         | Default                 | Purpose                                 |
|------------------|-------------------------|-----------------------------------------|
| `E2E_BASE_URL`   | (unset)                 | Use an already running app at this URL  |
| `E2E_SLOW_MO`    | `500` headed, `0` else  | Delay in ms between browser actions     |

On failure, Playwright keeps a trace and screenshot in `test-results/`. Open the report with `pnpm exec playwright show-report`.

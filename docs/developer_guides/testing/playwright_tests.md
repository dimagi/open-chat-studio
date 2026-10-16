# Playwright Tests

The end-to-end tests in `e2e/` drive the app in a browser to check the [core user stories](../../core_user_stories.md).

## Running the tests

Postgres and Redis must be running (`uv run inv up`) and the frontend assets built (`pnpm dev`).

```bash
pnpm install
pnpm exec playwright install chromium
pnpm e2e                     # headless
pnpm e2e --headed            # in a browser window, in slow motion
pnpm e2e --ui                # Playwright UI: pick a test and press play
pnpm e2e -g "<test title>"   # one test
```

Playwright starts a Django server on port 8010 and a Celery worker (chat replies and chatbot version creation are Celery tasks), and stops them when it exits. To test an app you already started, set `E2E_BASE_URL` (e.g. `http://localhost:8000`). That app needs a Celery worker, `DEBUG` on or `CI=true`, `USE_DEBUG_TOOLBAR=False` (the toolbar covers buttons the tests click), and the same database as the machine running Playwright. `E2E_SLOW_MO` sets the delay in ms between browser actions (default `500` headed, `0` otherwise).

On failure, Playwright keeps a trace and screenshot in `test-results/`; open the report with `pnpm exec playwright show-report`. The test data stays in the database until the next run, so you can log in as the scenario's user to see what a failed test left behind.

## Test data

Each test has its own scenario in `e2e/scenarios.ts`: a new `@e2e.test` user who owns a new team, plus the data for the state the scenario starts from.

| State          | Creates                                                                 |
|----------------|-------------------------------------------------------------------------|
| `team`         | A user (`<random>@e2e.test`) who owns a new team                        |
| `llm_provider` | `team`, plus an OpenAI provider that points at the mock LLM             |
| `chatbot`      | `llm_provider`, plus a chatbot whose pipeline uses that provider        |
| `session`      | `chatbot`, plus a web session from the user with one message and reply  |

Before the tests run, `e2e/global-setup.ts` runs `manage.py e2e_seed`. It deletes the previous run's data (every `@e2e.test` user and every team only they belong to) and creates every scenario. It refuses to run unless `DEBUG` is on or `CI=true`, because it creates users with a known password.

A test calls `await scenario("<name>")` (from `e2e/fixtures.ts`) to get its data and log the browser in as the scenario's user; `{ logIn: false }` leaves it logged out. Because no two tests share a scenario, tests run in any order and any one can run alone. In `--ui` mode, re-running a test reuses its scenario, including whatever the previous attempt changed.

The mock LLM is served by the dev server under `/mock-llm/` when `DEBUG` is on or `CI=true`. It needs no API key, makes no outside requests, and answers with lorem ipsum.

## Adding a test

1. Add the story to `docs/core_user_stories.md` with the next free `US-<n>` number and the spec file in "Covered by". Add the test to an existing spec when it continues a flow that spec already covers; otherwise create `e2e/tests/<area>.spec.ts`.
2. Add a scenario for the test to `SCENARIOS` in `e2e/scenarios.ts`, starting from the lowest state that has the data the test needs. If no state has it, add one (see below).
3. Write the test. Import `test` and `expect` from `../fixtures`, not from `@playwright/test`. Annotate it with the story numbers it covers, without the `US-` prefix: `{ annotation: { type: "user-stories", description: "8" } }` (comma-separated for several). Call `await scenario("<name>")` first, and build URLs from the data it returns rather than hard-coding team slugs or IDs. Prefer `getByRole` and `getByText` locators; fall back to `data-cy` or `input[name=...]` when the element has no accessible name.
4. Check it:

   ```bash
   pnpm e2e -g "<test title>"
   pnpm e2e:check-stories   # each story has an annotated, non-skipped test in its spec; CI runs this
   pnpm type-check          # includes `tsc -p e2e`
   ```

### Adding a state

Each state includes the ones before it, so add the new one after the state it builds on. Update these together:

- `State` in `apps/web/e2e_data.py`, and a step in `create_e2e_data` that creates the data and returns early for the new state
- the Python `E2EData` TypedDict in the same file, with a `NotRequired` field for each value a test reads
- `E2EState` and the `E2EData` interface in `e2e/scenarios.ts`, with the same field names
- the table of states in this guide

Create the data with the factories in `apps/utils/factories/`. Any user the state creates must have an `@e2e.test` email, so the next run deletes it.

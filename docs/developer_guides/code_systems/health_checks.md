# Health Checks

## Overview

"Health check" covers three separate mechanisms in OCS:

| Mechanism | What it checks | Who consumes it | Code |
|-----------|----------------|-----------------|------|
| [`/status/` endpoint](#status-endpoint) | OCS's own dependencies: database, cache, Redis, Celery workers | Load balancers, uptime monitors, operators | `apps/web/health_checks.py`, `apps/web/views.py` |
| [Celery worker ready file](#celery-worker-readiness-file) | That a Celery worker has started and is consuming its queues | Container orchestrator (ECS) | `config/celery.py` |
| [Custom action health checks](#custom-action-health-checks) | The external HTTP API behind a team's Custom Action | Team members (status column, notifications) | `apps/custom_actions/` |

The container-level checks in the Docker Compose files (`pg_isready`, `redis-cli ping`) only gate service start-up order. They are not part of the application.

## `/status/` endpoint

The endpoint is built on [`django-health-check`](https://codingjoe.dev/django-health-check/) (`health_check` is in `INSTALLED_APPS`). It is an async view, so checks run concurrently, and a synchronous `run()` method is executed in a thread by the library.

### URLs

Defined in `apps/web/urls.py`:

| Path | Checks | Use |
|------|--------|-----|
| `/status/` | `general` | Default. Load balancer target group health check |
| `/status/<subset>/` | The named subset | Targeted monitoring, debugging |

Subsets are defined in `CHECK_SUBSETS` in `apps/web/health_checks.py`:

| Subset | Checks |
|--------|--------|
| `general` | Database (`health_check.checks.Database`), Cache (`health_check.checks.Cache`), Redis (`health_check.contrib.redis.Redis`, using `settings.REDIS_URL`) |
| `celery` | One `CeleryQueueCheck` per entry in `apps.utils.celery.Queues` |
| `queue-<name>` | A single `CeleryQueueCheck`, where `<name>` is the lowercased `Queues` member name (`queue-chat`, `queue-background`, `queue-evaluations`) |

The `general` subset does not include Celery. A web container is therefore not marked unhealthy, and restarted, because workers are down or busy. Use the `celery` and `queue-*` subsets to monitor workers.

An unknown subset returns 404.

### Responses

The view is `apps.web.views.HealthCheck`, a subclass of `HealthCheckView`. The status code comes from the library: 200 when every check in the subset passes, 500 when any check fails. The body lists each check and its error. The `Accept` header selects the format (`text/plain`, `text/html`, `application/json`, and others); an unsupported value returns 406.

### Authentication

`HEALTH_CHECK_TOKENS` (comma-separated, default empty) secures every `/status/` URL.

- If the setting is empty, the endpoint is open.
- If it is set, requests must pass `?token=<value>` matching one of the tokens. A missing or wrong token returns **404**, not 401 or 403, so the endpoint does not reveal itself.

The load balancer config in the AWS guide uses `/status?token=...`. See [hosting configuration](../../hosting/configuration.md) and [AWS hosting](../../hosting/aws.md#health-check).

### Celery queue check

`CeleryQueueCheck` verifies that at least one worker is consuming a queue *and* is responsive. It makes two broadcast RPCs, each with a 3 second timeout:

1. `inspect.active_queues()` finds the workers whose active queues include the target queue name.
2. `control.ping(destination=<those workers>)` confirms at least one of them replies `{"ok": "pong"}`.

It raises `ServiceUnavailable` with `No worker for Celery queue '<label>' (<queue>)` if no worker consumes the queue or none answers the ping. A broker `OSError` is raised as `ServiceUnavailable("IOError")`.

The timeout is the main tuning concern. A slow reply is indistinguishable from no worker, so the timeout needs headroom for a busy broker, for example while a deploy restarts workers.

The check is per queue because production runs a dedicated worker per queue (see `Queues` in `apps/utils/celery.py`). A worker started without `-Q` consumes every queue in `settings.CELERY_TASK_QUEUES`, so a single-worker deployment passes every `queue-*` check. Declaring a new `Queues` member automatically adds it to the `celery` subset and creates its `queue-<name>` subset.

### Sentry

`/status/` paths are listed in `UNSAMPLED_PATH_PREFIXES` in `config/sentry.py`, so load balancer polling does not generate performance traces.

### Adding a check

1. Add the check to the relevant list in `apps/web/health_checks.py`. Either use a library check path (optionally as a `(path, kwargs)` tuple) or subclass `health_check.base.HealthCheck` and implement `run()`.
2. Raise `ServiceUnavailable` for a failure.
3. If it is a new subset, add it to `_check_subsets()`.
4. Add tests to `apps/web/tests/test_health_checks.py`. Cover the check in isolation and through the real view, because the view is async and a synchronous `run()` depends on the library's dispatch.

## Celery worker readiness file

Celery has no cheap readiness probe. `celery inspect ping` imports the whole app a second time, which exceeded the ECS container health check timeout on small workers and tripped the deployment circuit breaker. Instead the worker reports its own readiness (`config/celery.py`):

- If `CELERY_READY_FILE` is set, the `worker_ready` signal touches that file once the worker is consuming its queues.
- The `worker_shutdown` signal removes it.
- If the variable is unset (everywhere outside ECS), nothing is written.

The orchestrator's container health check tests for the file's existence. Tests are in `config/tests/test_celery_ready_file.py`.

## Custom action health checks

A `CustomAction` can have an optional health endpoint that OCS polls to show whether the external API is up.

### Model fields (`apps/custom_actions/models.py`)

| Field | Purpose |
|-------|---------|
| `healthcheck_path` | Path relative to `server_url`. Blank disables checking |
| `health_status` | `HealthCheckStatus`: `unknown` (default), `up`, `down` |
| `last_health_check` | Time of the last poll |

`CustomAction.health_endpoint` joins `server_url` and `healthcheck_path` into the full URL.

### Path detection

When the form is cleaned and no path was given, `CustomAction.detect_health_endpoint_from_spec()` looks in the OpenAPI schema for a `GET` operation at the first match of: `/health`, `/healthz`, `/healthcheck`, `/api/health`, `/status`, `/_health`. The detected path is saved as `healthcheck_path`.

### Polling (`apps/custom_actions/tasks.py`)

- Beat schedule `custom_actions.tasks.check_all_custom_actions_health` runs at minute 5 of every hour (`config/settings.py`). It enqueues `check_single_custom_action_health` for every action with a non-empty `healthcheck_path`.
- `check_single_custom_action_health` runs on the `background` queue. It issues an unauthenticated `httpx.get` with a 5 second timeout. A 2xx response is `up`. Any other status, or an `httpx.RequestError`, is `down`.
- It saves `health_status` and `last_health_check`.

### Notifications

A notification is created only on the transition to `down` from `up` or `unknown`. It is not repeated while the action stays down. `custom_action_health_check_failure_notification` (`apps/ocs_notifications/notifications.py`) sends an error-level notification to team members with the `custom_actions.change_customaction` permission, using slug `custom-action-health-check`. See [Notifications](notifications.md).

### Manual endpoint tester

The endpoint tester view in `apps/custom_actions/views.py` can move a status from `down` or `unknown` to `up` when the tested operation's path equals `healthcheck_path` and returns 2xx. It never sets `down`; only the polling task does, so the tester cannot overwrite the task's state or suppress its notification.

### UI

`templates/custom_actions/health_status_column.html` renders the status in the custom actions table (`apps/custom_actions/tables.py`).

## Key files

| File | Role |
|------|------|
| `apps/web/health_checks.py` | `CeleryQueueCheck`, `CHECK_SUBSETS` |
| `apps/web/views.py` | `HealthCheck` view, token handling, subset selection |
| `apps/web/urls.py` | `status/` routes |
| `apps/web/tests/test_health_checks.py` | Tests for the above |
| `config/settings.py` | `HEALTH_CHECK_TOKENS`, `CELERY_TASK_QUEUES`, beat schedule |
| `config/celery.py` | `CELERY_READY_FILE` signal handlers |
| `apps/custom_actions/tasks.py` | Custom action polling tasks |
| `docs/hosting/index.md` | Operator-facing summary |

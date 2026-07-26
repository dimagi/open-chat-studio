# Agents.md

This file provides guidance to AI coding agents when working with code in this repository.

## Project Overview

Open Chat Studio is a comprehensive platform for building, deploying, and evaluating AI-powered chat applications. It provides tools for working with various LLMs, creating chatbots, managing conversations, and integrating with different messaging platforms. Python 3.13+ required. Django project with Docker dev environment (`docker-compose-dev.yml`).

## Core Concepts

* Team: Multi-tenancy root; most resources scoped to a team
* Experiment: Versioned chat app with participants, channels, and configuration (user-facing name: Chatbot)
* Channel: Platform integration (Telegram, WhatsApp, Slack, API, web widget)
* Pipeline: DAG workflow (LLM nodes, routing, custom actions) executed during chat (core Chatbot functionality)
* Session/Chat: Participant conversation with message history
* Custom Action: HTTP API wrapper (OpenAPI schema) callable from pipelines
* Service Provider: Credentials for LLM, messaging, voice, and tracing services

## Architecture

* Multi-tenancy: `BaseTeamModel` pattern; team membership + Waffle flags for feature control
* Versioning: Experiments, Assistants, Pipelines support working/published versions via `VersionsMixin`
* Async tasks: Celery + Redis for background ops (sync, evaluations, media processing)
* API: DRF REST API (`/api/`) + OpenAI-compatible assistant endpoints
* Frontend: React/TS (webpack) + HTMX + Alpine.js in Django templates
* LLM abstraction: `LlmService` interface; supports OpenAI, Anthropic, Groq, Gemini, Azure, etc.
* Messaging abstraction: `MessagingService` + platform-specific clients with webhook routing
* Observability: Trace/Span models for request logging and pipeline step tracking

## Key Paths

* Django settings: `config/settings.py`
* Frontend build: `webpack.config.js`
* Package management: `pyproject.toml`, `package.json`
* Environment template: `.env.example`
* Django app root: `apps/` (see `docs/architecture/package-map.md` for what each app does and how dependencies flow)
* Django template root: `templates/`
* Shared FactoryBoy factories for test data generation: `apps/utils/factories/`
* Shared pytest fixtures: `apps/conftest.py`
* Javascript, Typescript and CSS files root: `assets/`
* Chat Widget component: `components/chat_widget` (standalone StencilJS component used by the Django app)

## Useful commands

* Run python tests: `uv run pytest path/to/test.py -v` (all tests in a file)
* Lint python: `uv run ruff check path/to/file.py --fix`
* Format python: `uv run ruff format path/to/file.py`
* Type check python: `uv run ty check apps/`
* Build JS & CSS: `pnpm run dev`
* Lint JS: `pnpm run lint path/to/file.js`
* TypeScript type checking: `pnpm run type-check path/to/file.ts`
* Run Django dev server: `uv run inv runserver` (uses `portless` if available, otherwise falls back to `uv run python manage.py runserver`)
* Django migrations: `uv run python manage.py migrate`
* Create migration: `uv run python manage.py makemigrations <app_name>`

## Verifying changes

Tests and typecheck are necessary but not sufficient. For any change with a runtime surface, exercise it end-to-end and observe the behaviour, not just green tests:

* Run the app with `uv run inv runserver` and drive the affected flow (the `/run` and `/verify` skills automate this if available).
* Chat/pipeline changes emit `Trace`/`Span` records (`apps/trace/models.py`) — inspect them to confirm a pipeline actually took the path you expect, rather than inferring from logs alone.
* For UI/UX regressions, the `dogfood` skill drives the browser and captures reproduction screenshots.
* When fixing a bug, reproduce it first (failing test or observed trace), then confirm the fix flips that same signal.

## Do
* Always lint, test, and typecheck updated files. Use project-wide build sparingly
* When adding new features: write or update unit tests first, then code to green
* For regressions: add a failing test that reproduces the bug, then fix to green
* Prefer `pytest.mark.parametrize` for tests over enumerated data (same assertion, varying inputs); give each case a readable ID with `pytest.param(..., id="...")` rather than an inline comment
* Always use @.github/pull_request_template.md as the template for pull request descriptions

## Ask first
Confirm with a human before these — they are hard to reverse or change a shared contract:
* Running `makemigrations`/`migrate` against anything but a local dev DB, or writing a data migration that mutates existing rows
* Deleting or removing a feature flag, or removing a public API field/endpoint
* Editing an accepted ADR (reverse a decision with a new superseding ADR instead — see `docs/adr/`)
* Adding a new LLM/messaging/service provider integration or a new third-party dependency

## Don't
* Do not local imports for any reason other than to avoid circular imports or as a means to reduce startup time (reserved for specific imports)
* Do not commit implementation plans to the repo unless asked

## Additional notes

Consult these guides when working in the relevant area:
* `docs/agents/django_model_auditing.md` — when adding or modifying audit logging on models
* `docs/agents/django_model_versioning.md` — when modifying versioned models (Experiment, Assistant, Pipeline)
* `docs/agents/django_performance.md` — when optimizing queries or addressing N+1 issues
* `docs/agents/django_view_security.md` — when adding or modifying views (permissions, auth)
* `docs/agents/multi_tenancy.md` — when adding new models or querysets (team scoping)
* `docs/agents/pipeline_repository.md` — when adding or modifying DB access in pipeline nodes
* `docs/developer_guides/code_systems/feature_flags.md` — when adding, using, or removing feature flags
* `docs/developer_guides/feature_deprecation.md` — when deprecating or removing a feature
* `docs/developer_guides/testing/help_agent_evals.md` — when adding or modifying help agents or their eval tests

## Agent skills

### Issue tracker

GitHub Issues on `dimagi/open-chat-studio` via the `gh` CLI.

### Domain docs

Single-context — `CONTEXT.md` and `docs/adr/` at the repo root (created lazily by `/grill-with-docs`). See `docs/agents/domain.md`.

### Architecture Decision Records (ADRs)

ADRs live at `docs/adr/` and are rendered into the docs site under Architecture → Decisions. Each ADR captures one decision with context, consequences, and rejected alternatives. ADRs are sequentially numbered (`0001-...`, `0002-...`) and immutable once accepted — reversing a decision means writing a new ADR that supersedes the old one.

Split decisions along the *independent supersession* axis: a choice you would revise on its own earns its own ADR; a choice that exists only as a forced consequence of a bigger decision (e.g. a stub library dictated by the type-checker you chose) is folded into that decision's ADR. Use `extends:` to link related-but-separate ADRs — relatedness alone is not a reason to split.

**Source-doc lifecycle.** Design and spec docs (anywhere under `docs/`) carry a `status` frontmatter field:

- `active` — still evolving; ADR extraction is gated off.
- `stable` — decisions are settled; safe to extract.
- `extracted` — already crystallised into ADRs; the source doc is now an index or has been deleted.

When you finish a design doc and ship the work, flip `status` from `active` to `stable`, then run the extraction skill.

**Extracting ADRs.** Use the `/extract-adrs <source-doc>` skill at `.claude/skills/extract-adrs/SKILL.md`. It walks you through identifying candidate decisions, drafting each ADR, wiring up cross-references, and updating `mkdocs.yml` plus `docs/adr/index.md`. The skill never commits — review the diff yourself.

**Writing an ADR by hand.** Copy `docs/adr/_template.md` to `docs/adr/NNNN-kebab-title.md` (next free number), fill it in, append a row to the `docs/adr/index.md` table, and add a nav entry under `Architecture → Decisions` in `mkdocs.yml`.

**Citing an ADR.** Use `ADR-NNNN` as the canonical reference in code comments, PR descriptions, and conversations. Link to the docs site URL for human-readable context.

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
* Django app root: `apps/`
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
* Build JS & CSS: `npm run dev`
* Lint JS: `npm run lint path/to/file.js`
* TypeScript type checking: `npm run type-check path/to/file.ts`
* Run Django dev server: `uv run inv runserver` (uses `portless` if available, otherwise falls back to `uv run python manage.py runserver`)
* Django migrations: `uv run python manage.py migrate`
* Create migration: `uv run python manage.py makemigrations <app_name>`

## Do
* Always lint, test, and typecheck updated files. Use project-wide build sparingly
* When adding new features: write or update unit tests first, then code to green
* For regressions: add a failing test that reproduces the bug, then fix to green
* Always use @.github/pull_request_template.md as the template for pull request descriptions

## Don't
* Use local imports for any reason other than to avoid circular imports or as a means to reduce startup time (reserved for specific imports)
* Don't commit implementation plans to the repo unless asked

## Additional notes

Consult these guides when working in the relevant area:
* `docs/agents/django_model_auditing.md` — when adding or modifying audit logging on models
* `docs/agents/django_model_versioning.md` — when modifying versioned models (Experiment, Assistant, Pipeline)
* `docs/agents/django_performance.md` — when optimizing queries or addressing N+1 issues
* `docs/agents/django_view_security.md` — when adding or modifying views (permissions, auth)
* `docs/agents/multi_tenancy.md` — when adding new models or querysets (team scoping)
* `docs/agents/feature_flags.md` — when adding, using, or removing feature flags

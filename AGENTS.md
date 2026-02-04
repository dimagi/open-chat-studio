# Agents.md

This file provides guidance to AI coding agents when working with code in this repository.

## Project Overview

Open Chat Studio is a comprehensive platform for building, deploying, and evaluating AI-powered chat applications. It provides tools for working with various LLMs, creating chatbots, managing conversations, and integrating with different messaging platforms.

* Backend: Django
* Frontend: React/TypeScript, TailwindCSS, AlpineJS, HTMX
* Database: PostgreSQL with pgvector extension
* Task Queue: Celery with Redis
* Package Management: uv (Python), npm (Node.js)
* CSS, JS & TS build: webpack
* Testing: pytest (python)

Key paths:

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

Useful commands:

* Run python tests: `pytest path/to/test.py -v` (all tests in a file)
* Lint python: `ruff check path/to/file.py --fix`
* Format python: `ruff format path/to/file.py`
* Build JS & CSS: `npm run dev`
* Lint JS: `npm run lint path/to/file.js`
* TypeScript type checking: `npm run type-check path/to/file.ts`

## Do
* Always lint, test, and typecheck updated files. Use project-wide build sparingly
* When adding new features: write or update unit tests first, then code to green
* For regressions: add a failing test that reproduces the bug, then fix to green

## Don't
* Use local imports for any reason other than to avoid circular imports or as a means to reduce startup time (reserved for specific imports)

## Additional notes

docs/agents/
  |- django_model_auditing.md
  |- django_model_versioning.md
  |- django_performance.md
  |- django_view_security.md
  |- multi_tenancy.md

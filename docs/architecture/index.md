---
hide:
  - navigation
---

# Architecture

This section provides an overview of the Open Chat Studio architecture, explaining the core concepts and components that make up the system.

Rather than duplicating details that can drift out of date, this page links to the sources engineers keep current: `AGENTS.md`, ADRs, `CONTEXT.md`, and the developer guides.

1. Significant architectural decisions are recorded as ADRs. See the **[ADR index](../adr/index.md)** for the full list.
2. For AI-generated architecture diagrams based on this GitHub repo, visit
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/dimagi/open-chat-studio)

## System Overview

Open Chat Studio is a multi-tenant platform: teams build and configure chatbots through the web UI, then publish them to reach **participants** — the end users who actually chat with a bot. It sits between two external ecosystems it doesn't own:
- **Messaging channels** — participants reach a chatbot over the web widget, Telegram, WhatsApp, Slack, email, CommCare Connect, or the API directly.
- **LLM service providers** — each team brings its own credentials for the LLM, voice, and tracing providers it wants to use.
Internally it's a modular Django app: the web process serves the UI, API, and channel webhooks synchronously, while Celery workers handle everything that shouldn't block a request (message processing, evaluations, document/media ingestion, scheduled events). For the production process topology and backing services (PostgreSQL, Redis), see the [Self-Hosting overview](../hosting/index.md#architecture-overview); for the third-party services that keep it observable in production, see [Monitoring & Observability](#monitoring-observability) below.

## Technology Stack

See the [README's Tech Stack](https://github.com/dimagi/open-chat-studio#tech-stack) for the full list of languages, frameworks, and deployment targets. A few stack choices carry architectural weight beyond that list:

- **LLM Abstraction**: [LangChain](https://python.langchain.com/) provides common chat model interfaces, message structures, and callback hooks that let a single `LlmService` layer work across providers.
- **Pipeline builder**: the DAG editor is built on [React Flow](https://reactflow.dev/)
- **Chat Widget**: standalone [StencilJS](https://stenciljs.com/) web component (`components/chat_widget`), built separately from the main app, embeddable in third-party sites

## Key Concepts

See the [Concepts User Documentation](https://docs.openchatstudio.com/concepts/) for product-facing definitions of Chatbots, Channels, Pipelines, Service Providers, and other concepts — or **[AGENTS.md → Core Concepts](https://github.com/dimagi/open-chat-studio/blob/main/AGENTS.md#core-concepts)** for the same concepts summarized for engineers.

For the precise domain language used in code (and by AI coding agents) — e.g. Chatbot vs. Chatbot Version, Working vs. Published Version, Session vs. Participant vs. User, Trace vs. Span — see **[CONTEXT.md](https://github.com/dimagi/open-chat-studio/blob/main/CONTEXT.md)**, the canonical glossary kept up to date as the domain model evolves.

## Project structure

The project is organized into several Django apps, each responsible for specific functionality. Apps are placed in the `apps` folder, and each app has its own models, views, serializers, and tests. See the **[package map](package-map.md)** for what each app does and how dependencies flow between them.

Not everything lives under `apps/` — for example, the chat widget (`components/chat_widget`) is a standalone StencilJS component with its own build. For the full, up-to-date inventory of key files and folders (settings, webpack config, package management, shared test fixtures/factories, etc.), see **[AGENTS.md → Key Paths](https://github.com/dimagi/open-chat-studio/blob/main/AGENTS.md#key-paths)**.

A couple of conventions worth knowing:

- **Django Templates** - Centralized in `templates/`; templates specific to an app go in `templates/{app_name}`.
- **Static Files** - Frontend source (TypeScript, JavaScript, CSS) lives in `assets/` (`assets/styles` for Tailwind config, `assets/javascript` for JS/TS modules) and is bundled with Webpack into the static assets served to users. Other static assets, like images, go directly in `static/`.

## Cross-Cutting Concerns

The architectural patterns underlying these concerns — multi-tenancy, versioning, async tasks, API design, LLM/messaging abstractions, and observability — are documented and kept up to date by engineers in **[AGENTS.md → Architecture](https://github.com/dimagi/open-chat-studio/blob/main/AGENTS.md#architecture)**. The key files below are useful pointers when working in these areas.

### Background Tasks

**Key Files**:

- `config/celery.py`: Celery configuration
- Various `tasks.py` files in different apps

### Authentication and Authorization

**Key Files**:

- `teams/middleware.py`: Team-based access control
- `teams/decorators.py`: Permission decorators

## Monitoring & Observability

Open Chat Studio relies on a small set of external services to keep production healthy: errors are captured and triaged in Sentry, Celery task execution is tracked in Task Badger, and overall uptime is monitored and communicated via BetterStack's status page.

### [Sentry](https://sentry.io/)

- **Purpose**: Error reporting and tracking
- Used for: Identifying and debugging production issues
- See more: [Sentry Configuration](../hosting/configuration.md#observability)

### [Task Badger](https://taskbadger.net/)

- **Purpose**: Celery task monitoring
- Used for: Monitoring asynchronous task execution and performance
- See more: [Task Badger configuration](../hosting/configuration.md#task-badger-optional)

### [BetterStack](https://betterstack.com/)

- **Purpose**: Uptime monitoring and status page
- Used for: Monitoring system availability and communicating status to users
- See more: Status Page: [status.openchatstudio.com](https://status.openchatstudio.com/)

---
hide:
  - navigation
---

# Architecture

This section provides an overview of the Open Chat Studio architecture, explaining the core concepts and components that make up the system.

!!! tip "Architecture Decision Records"

    Significant architectural decisions are recorded as ADRs. See the **[ADR index](../adr/index.md)** for the full list.

For AI-generated architecture diagrams based on this GitHub repo, visit
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/dimagi/open-chat-studio)

## System Overview

Open Chat Studio is built as a Django web application with a modular design. It consists of several Django apps that handle different aspects of the system.

## Technology Stack

- **Backend**: Python 3.13+, Django, Django REST Framework, Celery
- **Database**: PostgreSQL (with pgvector)
- **Cache/Message Broker**: Redis
- **LLM Abstraction**: [LangChain](https://python.langchain.com/) provides common chat model interfaces, message structures, and callback hooks that let a single `LlmService` layer work across providers
- **Frontend**: TypeScript, [ReactJS](https://react.dev/) (with [React Flow](https://reactflow.dev/) for pipeline building) and [htmx](https://htmx.org/)/[AlpineJS](https://alpinejs.dev/) in Django templates, bundled with Webpack, styled with [Tailwind](http://tailwindcss.com/) + [DaisyUI](https://daisyui.com/)
- **Chat Widget**: Standalone [StencilJS](https://stenciljs.com/) web component (`components/chat_widget`) embeddable in third-party sites
- **External LLM Services**: OpenAI, Anthropic, Groq, Gemini, Azure, and more — see the [full list](https://docs.openchatstudio.com/concepts/team/llm_providers/)
- **Deployment**: Docker, Heroku

## Key Concepts

See the [Concepts User Documentation](https://docs.openchatstudio.com/concepts/) for full definitions of Chatbots, Channels, Pipelines, Service Providers, and other product concepts.

One naming note for engineers reading the code: **Experiments/Chatbots** - 'Experiment' is the legacy name still used throughout the codebase (the `apps/experiments` app, the `Experiment` model, etc.). The UI and user docs refer to the same concept as a ['Chatbot'](https://docs.openchatstudio.com/concepts/chatbots/).

## Project structure

The project is organized into several Django apps, each responsible for specific functionality. Apps are placed in the `apps` folder, and each app has its own models, views, serializers, and tests. See the **[package map](package-map.md)** for what each app does and how dependencies flow between them.

- **Django Templates** - Templates as well as static files are centralized in the `templates` and `assets` folders, respectively. Templates specific to an app should be placed in the `templates/{app_name}` directory.

- **Static Files** - The `assets` folder contains JavaScript and CSS. The `assets/styles` folder contains Tailwind CSS configurations, while the `assets/javascript` folder contains JavaScript modules. These files are processed and bundled using Webpack to create the final static assets served to users. Other static assets like images are placed directly in the `static/` folder.

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

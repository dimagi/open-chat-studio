---
hide:
  - navigation
---
# Architecture

This section provides an overview of the Open Chat Studio architecture, explaining the core concepts and components that make up the system.

## System Overview

Open Chat Studio is built as a Django web application with a modular design. It consists of several Django apps that handle different aspects of the system.

## Technology Stack

- **Backend**: Django, Django REST Framework, Celery
- **Database**: PostgreSQL
- **Cache/Message Broker**: Redis
- **Frontend**: HTML, CSS ([Tailwind](http://tailwindcss.com/) + [DaisyUI](https://daisyui.com/)), [htmx](https://htmx.org/), [AlpineJS](https://alpinejs.dev/), [ReactJS](https://react.dev/) with [React Flow](https://reactflow.dev/) (for specific components)
- **External Services**: OpenAI, Azure, etc.

## Key Concepts

### Experiments

Experiments are configurations for AI chat experiences. They include:
- Prompts and LLM configurations
- Channel connections
- Data collection settings

!!! note

    The term 'Experiments' is a legacy term. On the user interface side, they are referred to as 'Chatbots'.

### Channels

Channels are communication interfaces that connect users to the chat system. These include:
- Web chat
- Slack
- WhatsApp
- Facebook Messenger
- Custom integrations

### Service Providers

Service providers enable integration with external services:
- LLM providers (OpenAI, Azure, etc.)
- Voice providers
- Authentication providers
- Messaging providers
- Tracing providers

### Pipelines

Pipelines allow for the creation of complex workflows with multiple nodes and processing steps.

## Project structure

The project is organized into several Django apps, each responsible for a specific functionality. Apps are placed in the `apps` folder, and each app has its own models, views, serializers, and tests. 

### Django Templates
Templates as well as static files are centralized in the `templates` and `assets` folders, respectively. Templates specific to an app should be placed in the `templates/{app_name}` directory.

### Static Files
The `assets` folder contains JavaScript, CSS. The `assets/styles` folder contains Tailwind CSS configurations, while the `assets/javascript` folder contains JavaScript modules. These files are processed and bundled using Webpack to create the final static assets served to users. Other static assets like images are placed directly in the `static/` folder.

## Cross-Cutting Concerns

### Background Tasks

Open Chat Studio uses Celery for asynchronous task processing, which is critical for handling LLM interactions, scheduled messages, and other background operations.

**Key Files**:

- `config/celery.py`: Celery configuration
- Various `tasks.py` files in different apps

### Authentication and Authorization

The system uses Django's authentication system along with custom middleware and decorators to ensure proper access control.

**Key Files**:

- `teams/middleware.py`: Team-based access control
- `teams/decorators.py`: Permission decorators

### Frontend Framework

The frontend uses a combination of Django templates, Tailwind CSS, and JavaScript to create a responsive and interactive user interface.

**Key Files**:

- `templates/`: HTML templates
- `assets/styles/`: CSS and Tailwind configurations
- `assets/javascript/`: JavaScript modules

## External services

**[Sentry](https://sentry.io/)**

- Purpose: Error reporting and tracking  
- Used for: Identifying and debugging production issues

**[Task Badger](https://taskbadger.net/)**  
- 
- Purpose: Celery task monitoring  
- Used for: Monitoring asynchronous task execution and performance

**[BetterStack](https://betterstack.com/)**

- Purpose: Uptime monitoring and status page  
- Status Page: [status.openchatstudio.com](https://status.openchatstudio.com/)  
- Used for: Monitoring system availability and communicating status to users

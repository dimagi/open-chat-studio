# Open Chat Studio - Claude Development Guide

Open Chat Studio is a comprehensive platform for building, deploying, and evaluating AI-powered chat applications. It provides tools for working with various LLMs, creating chatbots, managing conversations, and integrating with different messaging platforms.

## Project Overview

**Tech Stack:**
- **Backend**: Django 5.x with Python 3.11+
- **Frontend**: React/TypeScript, TailwindCSS, AlpineJS, HTMX
- **Database**: PostgreSQL with pgvector extension
- **Task Queue**: Celery with Redis
- **AI/ML**: LangChain, OpenAI, Anthropic, Google Gemini
- **Package Management**: uv (Python), npm (Node.js)

## Development Commands

### Backend (Django)
```bash
# Development server
inv runserver                    # Standard server
python manage.py runserver      # Direct Django command

# Database
python manage.py migrate        # Run migrations
python manage.py makemigrations # Create migrations
python manage.py shell          # Django shell

# Task queue
inv celery                      # Start Celery worker with beat
inv celery --gevent            # With gevent pool
inv celery --no-beat           # Without beat scheduler

# Testing
pytest                         # Run all tests
pytest apps/experiments/       # Run specific app tests
pytest -k test_name           # Run specific test
pytest --cov                  # With coverage
pytest -x                     # Stop on first failure

# Code quality
inv ruff                       # Run linting and formatting
inv ruff --no-fix             # Check only, don't fix
inv ruff --unsafe-fixes       # Apply unsafe fixes

# API schema
inv schema                     # Generate OpenAPI schema

# Translations
inv translations               # Extract and compile messages
```

### Frontend (Node.js/Webpack)
```bash
# Development builds
npm run dev                    # Build assets once
npm run dev-watch             # Build and watch for changes

# Production build
npm run build                 # Optimized production build

# Code quality
npm run lint                  # ESLint check and fix
npm run type-check            # TypeScript type checking
npm run type-check-watch      # Watch mode type checking
```

### Docker
```bash
inv up                        # Start PostgreSQL and Redis
inv down                      # Stop services
docker compose -f docker-compose-dev.yml up -d    # Direct command
```

### Dependencies
```bash
# Python
inv requirements              # Update lock file
inv requirements --upgrade-all # Upgrade all packages
inv requirements --upgrade-package <name> # Upgrade specific package
uv add <package>              # Add new package
uv remove <package>           # Remove package

# Node.js
npm install                   # Install dependencies
npm install <package>         # Add new package
npm uninstall <package>       # Remove package
```

## Django Apps Architecture

The project is organized into focused Django apps:

### Core Apps
- **`experiments/`** - Main experiment/chatbot management, sessions, participants
- **`chat/`** - Chat functionality, messages, conversation management
- **`channels/`** - Multi-platform messaging (WhatsApp, Slack, Telegram, etc.)
- **`pipelines/`** - Visual pipeline builder for complex chat workflows
- **`assistants/`** - OpenAI Assistants integration
- **`teams/`** - Multi-tenant team management and permissions

### Service & Integration Apps
- **`service_providers/`** - LLM providers (OpenAI, Anthropic, etc.), messaging, voice
- **`api/`** - REST API for external integrations
- **`slack/`** - Slack bot integration
- **`sso/`** - Single sign-on authentication
- **`channels/`** - Platform-specific messaging integrations

### Data & Content Apps
- **`documents/`** - File upload, processing, and collections
- **`files/`** - File management and metadata
- **`annotations/`** - User comments and tags
- **`analysis/`** - Data analysis and reporting

### Supporting Apps
- **`events/`** - Event-driven triggers and scheduled messages
- **`custom_actions/`** - User-defined API integrations
- **`participants/`** - Participant data management
- **`users/`** - User management and profiles
- **`web/`** - Base web functionality and admin
- **`audit/`** - Audit logging and tracking
- **`help/`** - Help system integration

## Key Features

### AI & LLM Integration
- **Multiple LLM Providers**: OpenAI, Anthropic, Google Gemini, DeepSeek
- **LangChain Integration**: Advanced prompt engineering and tool usage
- **Vector Stores**: Document search and retrieval
- **Function Calling**: Custom tools and API integrations

### Multi-Platform Messaging
- **Channels**: WhatsApp, Telegram, Slack, Facebook Messenger
- **Web Chat**: Embeddable chat widgets
- **Voice Support**: Speech-to-text and text-to-speech

### Advanced Features
- **Visual Pipeline Builder**: React-based flow editor for complex conversations
- **Versioning System**: Track changes to experiments and configurations
- **Event System**: Triggers, scheduled messages, timeout handling
- **Team Management**: Multi-tenant with role-based permissions
- **Document Processing**: PDF, DOCX, TXT with AI-powered summaries

## Testing

### Test Structure
- Tests use **pytest** with Django integration
- Fixtures in `apps/conftest.py` and app-specific `conftest.py`
- Factory Boy for test data generation
- Coverage reporting available

### Test Configuration
- Settings: `gpt_playground.settings` with test overrides
- Database: `--reuse-db` for faster test runs
- Environment variables automatically set in tests

## Configuration

### Environment Variables
Key settings in `.env` file:

### Django Settings
- **Base**: `gpt_playground/settings.py`
- **Production**: `gpt_playground/settings_production.py`
- **Debug**: Enabled by default in development
- **Database**: PostgreSQL with pgvector for embeddings

## Code Style & Standards

### Python
- **Linting**: Ruff with automatic fixing
- **Formatting**: Ruff format (Black-compatible)
- **Line Length**: 120 characters
- **Import Sorting**: isort via Ruff

### JavaScript/TypeScript
- **Linting**: ESLint with TypeScript support
- **Type Checking**: TypeScript strict mode
- **Build**: Webpack with Babel

### Django Conventions
- **Models**: Use model audit fields for tracking changes
- **Views**: Class-based views with mixins
- **Templates**: Django templates with HTMX for interactivity
- **URLs**: App-specific URL configurations

## Development Patterns

### Versioning System
Models can be versioned using the built-in versioning system:
- Inherit from `VersionsMixin`
- Add `working_version` field (self-referencing FK)
- Add `is_archived` field
- Objects are archived, not deleted

### Team-Based Multi-Tenancy
- All data is scoped to teams
- Use `@login_and_team_required` or `@team_required` decorators on views
- Team context available in middleware
- Permission system based on team membership

### Task Queue (Celery)
- Background tasks for AI processing
- Scheduled messages and events
- Progress tracking with celery-progress
- Redis as broker and result backend

### API Design
- DRF-based REST API
- OpenAPI schema generation
- API key authentication
- Cursor-based pagination

## Deployment

### Production Considerations
- Use `settings_production.py` for production settings
- Configure external services (S3, email provider)
- Set up proper logging and monitoring
- Use gunicorn with gevent workers

### Environment Setup
- PostgreSQL with pgvector extension
- Redis for caching and task queue
- File storage (local or S3)
- HTTPS with proper domain configuration

## Troubleshooting

### Common Issues
1. **Node version**: Ensure Node.js 18+ is installed
2. **Database connection**: Verify PostgreSQL is running
3. **Redis connection**: Check Redis service status
4. **Missing migrations**: Run `python manage.py migrate`
5. **Asset building**: Run `npm run dev` after dependency changes

### Debug Mode
- Set `DEBUG=True` in `.env` for development
- Use Django debug toolbar for SQL query analysis
- Check Celery logs for background task issues

## Resources

- **Documentation**: https://docs.openchatstudio.com
- **Developer Docs**: https://developers.openchatstudio.com
- **Repository**: https://github.com/dimagi/open-chat-studio
- **Issues**: https://github.com/dimagi/open-chat-studio/issues

## Key Files Reference

- **Main Django config**: `gpt_playground/settings.py`
- **Task definitions**: `tasks.py` (Invoke commands)
- **Frontend build**: `webpack.config.js`
- **Package management**: `pyproject.toml`, `package.json`
- **Docker services**: `docker-compose-dev.yml`
- **Environment template**: `.env.example`

# Open Chat Studio - Claude Development Guide

Open Chat Studio is a comprehensive platform for building, deploying, and evaluating AI-powered chat applications. It provides tools for working with various LLMs, creating chatbots, managing conversations, and integrating with different messaging platforms.

## Project Overview

**Tech Stack:**
- **Backend**: Django 5.x with Python 3.13+
- **Frontend**: React 19/TypeScript, TailwindCSS 4.x, DaisyUI 5.x, Alpine.js, HTMX
- **Database**: PostgreSQL with pgvector extension
- **Task Queue**: Celery with Redis
- **AI/ML**: LangChain 0.3.x, LangGraph, OpenAI, Anthropic, Google Gemini, DeepSeek
- **Observability**: LangFuse for LLM tracing, Sentry for error tracking
- **Package Management**: uv (Python), npm (Node.js)

## Development Commands

### Backend (Django)
```bash
# Development server
inv runserver                    # Standard server
inv runserver --public          # With ngrok tunnel for external access
python manage.py runserver      # Direct Django command

# Database
python manage.py migrate        # Run migrations
python manage.py makemigrations # Create migrations
python manage.py shell          # Django shell

# Task queue
inv celery                      # Start Celery worker with beat
inv celery --gevent            # With gevent pool (no beat)
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
inv ruff --paths "apps/chat"  # Check specific paths

# API schema
inv schema                     # Generate OpenAPI schema

# Translations
inv translations               # Extract and compile messages

# Full setup
inv setup-dev-env             # Complete dev environment setup
inv setup-dev-env --step      # Interactive step-by-step setup
```

### Frontend (Node.js/Webpack)
```bash
# Development builds
npm run dev                    # Build assets once
npm run dev-watch             # Build and watch for changes
inv npm --watch               # Same as above via invoke

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
uv sync --frozen --dev        # Sync environment with lock file

# Node.js
npm install                   # Install dependencies
npm install <package>         # Add new package
npm uninstall <package>       # Remove package
```

## Django Apps Architecture

The project contains 31 Django apps in `/home/user/open-chat-studio/apps/`, organized into focused functional areas:

### App File Structure
Each app follows this pattern:
```
app_name/
├── __init__.py
├── admin.py              # Django admin configuration
├── apps.py               # App configuration
├── forms.py              # Form definitions
├── models.py             # Model definitions
├── tables.py             # Django-tables2 definitions
├── urls.py               # URL patterns
├── views/                # View modules (for complex apps)
│   ├── __init__.py
│   └── specific_views.py
├── migrations/           # Database migrations
├── tests/                # Test modules
├── templatetags/         # Custom template tags
├── management/           # Custom management commands
├── const.py              # Application constants
├── exceptions.py         # Custom exceptions
└── tasks.py              # Celery task definitions
```

### Core Apps
- **`experiments/`** - Main experiment/chatbot management, sessions, participants, versioning, routing, surveys, consent forms
- **`chatbots/`** - Simplified chatbot interface (uses Experiment model with streamlined UX)
- **`chat/`** - Chat functionality, messages, conversation management, metadata
- **`channels/`** - Multi-platform messaging (Telegram, WhatsApp, Facebook, Web, API, Slack, CommCare Connect)
- **`pipelines/`** - Visual pipeline builder for complex chat workflows with versioning
- **`assistants/`** - OpenAI Assistants integration
- **`teams/`** - Multi-tenant team management, memberships, permissions

### AI & Evaluation Apps
- **`service_providers/`** - LLM providers (OpenAI, Anthropic, Google Gemini, DeepSeek), messaging providers, voice providers, embedding models
- **`evaluations/`** - Evaluation framework for testing chatbots with multiple evaluator types, datasets, and runs
- **`analysis/`** - Transcript analysis jobs with LLM-powered queries and translation support
- **`mcp_integrations/`** - Model Context Protocol server integration for tool discovery and syncing

### Data & Content Apps
- **`documents/`** - File upload, processing, collections with versioning, chunking strategies, vector stores
- **`files/`** - File management, metadata, S3 storage integration
- **`annotations/`** - User comments, tags, custom tags

### Supporting Apps
- **`events/`** - Event-driven triggers, scheduled messages, timeout handling
- **`custom_actions/`** - User-defined API integrations
- **`participants/`** - Participant data management
- **`filters/`** - Saved filter configurations for tables (sessions, datasets, participants, traces)

### Infrastructure Apps
- **`api/`** - REST API with DRF, OpenAPI schema, API key authentication
- **`users/`** - User management and profiles
- **`admin/`** - Admin functionality and customization
- **`web/`** - Base web functionality, locale/HTMX middleware
- **`sso/`** - Single sign-on authentication
- **`slack/`** - Slack bot integration

### Observability & Monitoring Apps
- **`trace/`** - Observability system with Trace and Span models for performance monitoring
- **`audit/`** - Audit logging using django-field-audit for field-level change tracking
- **`dashboard/`** - Analytics and dashboard views

### Utility Apps
- **`generics/`** - Generic utilities (actions, chips, middleware, exception handling)
- **`banners/`** - Site/user banners
- **`help/`** - Help system integration
- **`utils/`** - Shared utilities, base model classes, factories

## Key Features

### AI & LLM Integration
- **Multiple LLM Providers**: OpenAI, Anthropic, Google Gemini, DeepSeek
- **LangChain Integration**: Advanced prompt engineering and tool usage (0.3.x)
- **LangGraph**: Agent workflows and complex reasoning chains
- **MCP Integration**: Model Context Protocol for external tool servers
- **Vector Stores**: Document search and retrieval with pgvector
- **Function Calling**: Custom tools and API integrations

### Multi-Platform Messaging
- **Channels**: WhatsApp, Telegram, Slack, Facebook Messenger
- **Web Chat**: Embeddable chat widgets
- **Voice Support**: Speech-to-text and text-to-speech (Azure Cognitive Services)

### Evaluation Framework
- **Evaluators**: LLM-based evaluation, pattern matching, custom criteria
- **Datasets**: Create test datasets for systematic testing
- **Evaluation Runs**: Full and preview modes for testing chatbots
- **Results Tracking**: Comprehensive results and metrics

### Advanced Features
- **Visual Pipeline Builder**: React Flow-based editor for complex conversations
- **Versioning System**: Track changes to experiments, pipelines, and documents
- **Event System**: Triggers, scheduled messages, timeout handling
- **Team Management**: Multi-tenant with role-based permissions
- **Document Processing**: PDF, DOCX, XLSX, PPTX, Outlook with MarkItDown

### Observability
- **LangFuse Integration**: LLM tracing and monitoring
- **Trace System**: Internal performance monitoring with spans
- **Field Audit**: Track changes to important model fields
- **Sentry**: Error tracking and performance monitoring

## Testing

### Test Structure
- Tests use **pytest** with Django integration
- Fixtures in `apps/conftest.py` and app-specific `conftest.py`
- Factory Boy for test data generation
- Coverage reporting available
- Database reuse with `--reuse-db` for faster tests

### Common Test Fixtures

Check for existing fixtures in `conftest.py` files and use existing factories in `apps/utils/factories/` package:

```python
# Team-based fixtures (most tests need these)
@pytest.fixture()
def team(db):
    return TeamFactory.create()

@pytest.fixture()
def team_with_users(db):
    return TeamWithUsersFactory.create()  # Includes admin + member

@pytest.fixture()
def experiment(team_with_users, db):
    return ExperimentFactory(team=team_with_users)

# Mock fixtures for external services
@pytest.fixture()
def remote_index_manager_mock():
    # Mocks LLM provider's remote index manager
    ...

@pytest.fixture()
def local_index_manager_mock():
    # Mocks LLM provider's local index manager
    ...
```

### Available Factories
Located in `apps/utils/factories/`:
- **team.py**: `TeamFactory`, `TeamWithUsersFactory`
- **user.py**: `UserFactory`
- **experiment.py**: `ExperimentFactory`
- **channels.py**: Channel-related factories
- **pipelines.py**: Pipeline factories
- **documents.py**: Document and collection factories
- **files.py**: File factories
- **assistants.py**: Assistant factories
- **evaluations.py**: Evaluation, dataset, evaluator factories
- **events.py**: Event and trigger factories
- **custom_actions.py**: Custom action factories
- **mcp_integrations.py**: MCP integration factories
- **traces.py**: Trace and span factories
- **service_provider_factories.py**: LLM provider factories
- **openai.py**: OpenAI-specific factories

### Test Organization
- Tests in `tests/` directories within each app
- Separate test files: `test_models.py`, `test_views.py`, `test_api.py`, `test_forms.py`
- Mock external services (LLM providers, etc.)
- Team isolation in all tests

### Test Configuration
- Settings: `config.settings` with test overrides
- pytest options: `--ds=config.settings --reuse-db --strict-markers --tb=short`
- Environment: `UNIT_TESTING=True` set automatically

## Configuration

### Environment Variables
Key settings in `.env` file such as database connection strings, credentials for external services, etc. Copy `.env.example` to `.env` for initial setup.

### Django Settings
- **Base**: `config/settings.py`
- **Production**: `config/settings_production.py`
- **Debug**: Enabled by default in development
- **Database**: PostgreSQL with pgvector for embeddings

## Code Style & Standards

### Python
- **Linting**: Ruff with automatic fixing
- **Formatting**: Ruff format (Black-compatible)
- **Line Length**: 120 characters
- **Import Sorting**: isort via Ruff
- **Rules**: E, F, I, UP, DJ, PT, B, SIM

### JavaScript/TypeScript
- **Linting**: ESLint with TypeScript support
- **Type Checking**: TypeScript 5.6 strict mode
- **Build**: Webpack 5 with Babel

### Django Templates
- **Linting**: djlint with Django profile
- **Indentation**: 2 spaces
- **Format**: `format_attribute_template_tags=true`

### Django Conventions
- **Models**: Use model audit fields for tracking changes
- **Views**: Class-based views with mixins
- **Templates**: Django templates with HTMX and Alpine.js for interactivity
- **URLs**: App-specific URL configurations

### UI/Frontend Guidelines
- **Design System**: Use DaisyUI components and styling as the primary UI framework
- **Fallback Styling**: Use TailwindCSS for custom styling when DaisyUI components don't meet requirements
- **Theme Support**: All UI components must support both light and dark modes
- **Component Priority**: Always prefer DaisyUI widgets over custom implementations

### Git Usage Guidelines
- **Logical Commits**: Break work into logical chunks and commit after completing each coherent piece of functionality
- **Commit Messages**: Write concise commit messages that describe the change's purpose, not an exhaustive list of modifications
- **Commit Frequency**: Commit regularly to create a clear development history and enable easy rollbacks
- **Message Format**: Use imperative mood (e.g., "Add user authentication" not "Added user authentication")

## Development Patterns

### Model Architecture Patterns

#### Base Model Classes
Most models should inherit from appropriate base classes:
```python
# For team-scoped models (most common)
from apps.teams.models import BaseTeamModel
class MyModel(BaseTeamModel):
    # Automatically includes team FK and filtering
    # Also includes created_at, updated_at timestamps
    pass

# For non-team models (rare)
from apps.utils.models import BaseModel
class MyModel(BaseModel):
    # Includes created_at, updated_at timestamps
    pass
```

#### Versioning System
Complex models that need version tracking (used by Experiment, Pipeline, Document.Collection):
```python
from apps.teams.models import BaseTeamModel
from apps.experiments.versioning import VersionsObjectManagerMixin, VersionsMixin, VersionDetails, VersionField

class MyModelManager(VersionsObjectManagerMixin):
    pass

class MyModel(BaseTeamModel, VersionsMixin):
    working_version = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True)
    is_archived = models.BooleanField(default=False)

    # Optional version number
    version_number = models.PositiveIntegerField(default=1)

    objects = MyModelManager()

    # Objects are archived, not deleted
    # Provides diff detection and comparison

    def _get_version_details(self) -> VersionDetails:
        """Return details used for comparing versions"""
        return VersionDetails(
            instance=self,
            fields=[
                VersionField(group_name="General", name="field_a", raw_value=self.field_a),
                VersionField(group_name="General", name="field_b", raw_value=self.field_b),
            ],
        )
```

#### Field Audit Pattern
Track field changes on important models using django-field-audit:
```python
from field_audit import audit_fields
from field_audit.models import AuditingManager

@audit_fields("field_a", "field_b", audit_special_queryset_writes=True)
class MyModel(BaseTeamModel):
    objects = AuditingManager()
    # Changes tracked automatically
```

#### Common Field Patterns
```python
# Public API identifier
public_id = models.UUIDField(default=uuid.uuid4, unique=True)

# Encrypted sensitive data
from django_cryptography.fields import encrypt
encrypted_field = encrypt(models.JSONField(blank=True))

# PostgreSQL-specific fields
from django.contrib.postgres.fields import ArrayField
tags = ArrayField(models.CharField(max_length=50), default=list, blank=True)

# Pydantic validation
from django_pydantic_field import SchemaField
config = SchemaField(schema=MyPydanticModel)
```

### View Security Patterns

#### Required Decorators
Always use team-based security:
```python
from apps.teams.decorators import login_and_team_required
from django.contrib.auth.decorators import permission_required

# Function-based views
@login_and_team_required
@permission_required("my_app.view_mymodel")
def my_view(request, team_slug: str):
    # request.user and request.team are available
    pass

# Class-based views
from apps.teams.mixins import LoginAndTeamRequiredMixin
from django.contrib.auth.mixins import PermissionRequiredMixin

class MyView(LoginAndTeamRequiredMixin, CreateView, PermissionRequiredMixin):
    permission_required = "my_app.view_mymodel"
```

#### URL Patterns
Team-scoped URLs with consistent naming:
```python
# urls.py
urlpatterns = [
    path("teams/<slug:team_slug>/my-feature/", MyView.as_view(), name="my_feature"),
    path("teams/<slug:team_slug>/my-feature/<int:pk>/", MyDetailView.as_view(), name="my_feature_detail"),
]
```

### Form Patterns

#### Team-Scoped Forms
Always filter related fields by team:
```python
class MyForm(forms.ModelForm):
    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = request.team if request else None

        # Filter all related model querysets by team
        if self.team:
            self.fields['related_model'].queryset = RelatedModel.objects.filter(team=self.team)

    class Meta:
        model = MyModel
        fields = ['name', 'related_model']
```

### Template Organization

#### File Structure
```
templates/
├── web/
│   ├── base.html                    # Base template
│   ├── app/
│   │   └── app_base.html            # App layout with navigation
│   └── components/                  # Reusable components
├── generic/
│   └── chip.html                    # Generic components
├── {app_name}/
│   ├── {model}_form.html           # Form templates
│   ├── {model}_list.html           # List templates
│   ├── {model}_detail.html         # Detail templates
│   └── components/                 # App-specific components
```

#### Common Template Patterns
```html
<!-- Extend app_base.html for overall layout and navigation -->
{% extends "web/app/app_base.html" %}

{% block page_head %}
  {{ block.super }}
  <!-- Custom 'head' contents e.g. page-specific stylesheets -->
{% endblock page_head %}

{% block app %}
  <!-- Page specific content -->
{% endblock app %}

{% block page_js %}
  {{ block.super }}
  <!-- Page-specific JavaScript -->
{% endblock page_js %}

<!-- HTMX for dynamic content -->
<div hx-get="{% url 'app:view-name' team.slug %}" hx-trigger="load">
    Loading...
</div>

<!-- Reusable components -->
{% include "generic/chip.html" with object=my_object %}
```

### API Patterns

#### Team Filtering
Always filter API responses by team:
```python
class MyViewSet(viewsets.ModelViewSet):
    def get_queryset(self):
        return MyModel.objects.filter(team=self.request.team)
```

#### Serializer Patterns
```python
class MySerializer(serializers.ModelSerializer):
    # Expose public_id as 'id' for external API
    id = serializers.UUIDField(source="public_id", read_only=True)
    url = serializers.HyperlinkedIdentityField(view_name="api:my-model-detail")

    class Meta:
        model = MyModel
        fields = ["id", "name", "url"]
        read_only_fields = ["id"]
```

### Celery Task Patterns

#### Basic Task
```python
from celery.app import shared_task

@shared_task(ignore_result=True)
def notify_task(experiment_session_id: int, safety_layer_id: int):
    experiment = ExperimentSession.objects.get(id=experiment_session_id)
    # ... do work
```

#### Progress-Tracked Task
```python
from taskbadger.celery import Task as TaskbadgerTask

@shared_task(bind=True, base=TaskbadgerTask)
def async_export_chat(self, experiment_id: int, query_params: dict, time_zone):
    experiment = Experiment.objects.get(id=experiment_id)
    # ... do work with progress tracking
    return {"file_id": file_obj.id}
```

#### Task with Team Context
```python
from apps.teams.utils import current_team

@shared_task
def some_task(experiment_id: int):
    experiment = Experiment.objects.prefetch_related("assistant", "pipeline").get(id=experiment_id)
    with current_team(experiment.team):
        # ... operations in team context
```

### Team-Based Multi-Tenancy
- All data is scoped to teams via `BaseTeamModel`
- Use `@login_and_team_required` or `@team_required` decorators on views
- Team context available in middleware as `request.team`
- Permission system based on team membership
- Never allow cross-team data access without explicit permission
- Use `current_team()` context manager for async operations

### API Design
- DRF-based REST API
- OpenAPI schema generation with drf-spectacular
- API key authentication via djangorestframework-api-key
- Cursor-based pagination available

## Key Dependencies

### AI & LLM
- **LangChain** 0.3.x with anthropic, openai, google_genai, community
- **LangGraph** >=0.2.20 for agent workflows
- **LangFuse** >=2.59.7 for tracing/observability
- **langchain-mcp-adapters** for MCP integration
- **tiktoken** for token counting

### Database
- **PostgreSQL** with pgvector extension
- **psycopg** native Python driver with pool

### Web Framework
- **Django** 5.x
- **DRF** with drf-spectacular
- **django-tables2**, django-taggit
- **django-field-audit** for audit trails
- **django-cryptography-django5** for field encryption
- **django-waffle** for feature flags
- **django-htmx** for HTMX integration

### Frontend
- **React** 19.2 with React Flow
- **TailwindCSS** 4.x with DaisyUI 5.x
- **Alpine.js** 3.x for lightweight interactivity
- **HTMX** 2.x for dynamic HTML
- **CodeMirror** for code editors

### Task Queue & Messaging
- **Celery** with Redis broker
- **django_celery_beat** for scheduled tasks
- **taskbadger** for task monitoring
- **Twilio**, **pyTelegramBotAPI**, **slack-bolt**, **turn-python** (WhatsApp)

### File Processing
- **boto3** + django-storages for S3
- **MarkItDown** for document parsing (PDF, DOCX, XLSX, PPTX, Outlook)
- **python-magic** for file type detection

### Monitoring
- **Sentry SDK** for error tracking
- **django-debug-toolbar** for development
- **django-silk** for profiling

## Troubleshooting

### Common Issues
1. **Python version**: Ensure Python 3.13+ is installed
2. **Node version**: Ensure Node.js 18+ is installed
3. **Database connection**: Verify PostgreSQL is running (`inv up`)
4. **Redis connection**: Check Redis service status
5. **Missing migrations**: Run `python manage.py migrate`
6. **Asset building**: Run `npm run dev` after dependency changes
7. **Missing .env**: Copy `.env.example` to `.env`

### Debug Mode
- Set `DEBUG=True` in `.env` for development
- Use Django debug toolbar for SQL query analysis
- Use django-silk for request profiling
- Check Celery logs for background task issues

## Resources

- **Documentation**: https://docs.openchatstudio.com
- **Developer Docs**: https://developers.openchatstudio.com
- **Repository**: https://github.com/dimagi/open-chat-studio
- **Issues**: https://github.com/dimagi/open-chat-studio/issues
- **Changelog**: https://docs.openchatstudio.com/changelog/

## Key Files Reference

- **Main Django config**: `config/settings.py`
- **Production settings**: `config/settings_production.py`
- **Task definitions**: `tasks.py` (Invoke commands)
- **Frontend build**: `webpack.config.js`
- **Package management**: `pyproject.toml`, `package.json`
- **Lock files**: `uv.lock`, `package-lock.json`
- **Docker services**: `docker-compose-dev.yml`
- **Environment template**: `.env.example`
- **pytest config**: `pyproject.toml` [tool.pytest.ini_options]
- **Ruff config**: `pyproject.toml` [tool.ruff]
- **djlint config**: `pyproject.toml` [tool.djlint]

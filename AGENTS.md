# Open Chat Studio - Developer Guide

Open Chat Studio is a comprehensive platform for building, deploying, and evaluating AI-powered chat applications. It provides tools for working with various LLMs, creating chatbots, managing conversations, and integrating with different messaging platforms.

## Project Overview

**Tech Stack:**
- **Backend**: Django 5.x with Python 3.13+
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

The project is organized into focused Django apps with consistent structure:

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
- **`utils/`** - Shared utilities and base classes

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

### Common Test Fixtures

* Check for existing fixtures in `conftest.py` files
* Use existing factories in `apps/utils/factories/` package

```python
# Team-based fixtures (most tests need these)
@pytest.fixture()
def team_with_users(db):
    return TeamWithUsersFactory.create()

@pytest.fixture()
def experiment(team_with_users, db):
    return ExperimentFactory(team=team_with_users)

# Use factories for consistent test data
class MyModelFactory(factory.django.DjangoModelFactory):
    team = factory.SubFactory(TeamFactory)
    name = factory.Sequence(lambda n: f'Test Model {n}')
    
    class Meta:
        model = MyModel
```

### Test Organization
- Tests in `tests/` directories within each app
- Separate test files: `test_models.py`, `test_views.py`, `test_api.py`
- Mock external services (LLM providers, etc.)
- Team isolation in all tests

### Test Configuration
- Settings: `config.settings` with test overrides
- Environment variables automatically set in tests

## Configuration

### Environment Variables
Key settings in `.env` file such as database connection strings, credentials for external services, etc.

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

### JavaScript/TypeScript
- **Linting**: ESLint with TypeScript support
- **Type Checking**: TypeScript strict mode
- **Build**: Webpack with Babel

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

### Git Hooks
- The repository uses pre-commit hooks to enforce code style and quality.
- After setting up the environment, run `pre-commit install` to install the hooks.

### Git Usage Guidelines
- **Logical Commits**: Break work into logical chunks and commit after completing each coherent piece of functionality
- **Commit Messages**: Write concise commit messages that describe the change's purpose, not an exhaustive list of modifications
- **Commit Frequency**: Commit regularly to create a clear development history and enable easy rollbacks
- **Message Format**: Use imperative mood (e.g., "Add user authentication" not "Added user authentication")

## Development Patterns

### HTTP Requests
Use `httpx` instead of `requests` for making HTTP calls. `httpx` provides:
- Async/await support for non-blocking I/O
- Better performance in Celery tasks and async contexts
- Consistent API with optional request/response hooks
- Built-in timeout defaults for safety

```python
# ❌ Avoid
import requests
response = requests.get("https://api.example.com/data")

# ✅ Prefer
import httpx
response = httpx.get("https://api.example.com/data", timeout=10.0)
```

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
Complex models that need version tracking:
```python
from apps.teams.models import BaseTeamModel
from apps.experiments.versioning import VersionsObjectManagerMixin, VersionsMixin, VersionDetails, VersionField

class MyModelManager(VersionsObjectManagerMixin):
    pass

class MyModel(BaseTeamModel, VersionsMixin):
    working_version = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True)
    is_archived = models.BooleanField(default=False)
    
    # optional version number
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
Track field changes on important models:
```python
from apps.audit.decorators import audit_fields

class MyModelManager(AuditingManager):
    pass

@audit_fields("field_a", "field_b", audit_special_queryset_writes=True)
class MyModel(BaseTeamModel):
    # Define audit fields in model_audit_fields.py
    objets = MyModelManager()
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
    def __init__(self, *args, **kwargs):
        self.team = kwargs.pop('team', None)
        super().__init__(*args, **kwargs)
        
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
│   └── components/                  # Reusable components
├── {app_name}/
│   ├── {model}_form.html           # Form templates
│   ├── {model}_list.html           # List templates
│   ├── {model}_detail.html         # Detail templates
│   └── components/                 # App-specific components
```

#### Common Template Patterns
```html
<!-- Extend app_base.html overall layout and navigation -->
{% extends "web/app/app_base.html" %}

{% block page_head %}
  {{ block.super }}
  <!-- custom 'head' contents e.g. page-specific stylesheets -->
{% endblock page_head %}

{% block app %}
  <!-- page specific content -->
{% endblock app %}

{% block page_js %}
  {{ block.super }}
  <!-- page-specific JavaScript -->
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

### Performance Patterns

#### Lazy Loading Heavy Imports
Avoid importing heavy AI/ML libraries at module level to keep Django startup time fast:
```python
# ❌ BAD - imports at module level (slow startup)
from langchain_google_vertexai import ChatVertexAI
from langchain_anthropic import ChatAnthropic

def get_model():
    return ChatVertexAI(...)

# ✅ GOOD - lazy import inside method (fast startup)
def get_model():
    from langchain_google_vertexai import ChatVertexAI
    return ChatVertexAI(...)
```

Heavy libraries that benefit from lazy loading:
- `langchain_google_vertexai` (~45s import time)
- `langchain_google_genai` (~9s import time)
- `langchain_anthropic`, `langchain_openai` (~3s combined)
- `boto3`, `pandas`, `numpy` (when not always needed)

Use `TYPE_CHECKING` for type hints only:
```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_google_vertexai import ChatVertexAI
```

### Team-Based Multi-Tenancy
- All data is scoped to teams via `BaseTeamModel`
- Use `@login_and_team_required` or `@team_required` decorators on views
- Team context available in middleware as `request.team`
- Permission system based on team membership
- Never allow cross-team data access without explicit permission

### Task Queue (Celery)
- Background tasks for AI processing, data exports
- Scheduled messages and events
- Progress tracking with celery-progress
- Redis as broker and result backend

### API Design
- DRF-based REST API
- OpenAPI schema generation
- API key authentication
- Cursor-based pagination

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

- **Main Django config**: `config/settings.py`
- **Task definitions**: `tasks.py` (Invoke commands)
- **Frontend build**: `webpack.config.js`
- **Package management**: `pyproject.toml`, `package.json`
- **Docker services**: `docker-compose-dev.yml`
- **Environment template**: `.env.example`

import pytest
from django.conf import settings

from apps.api.openai import ChatCompletionsView
from apps.api.permissions import BASE_PERMISSION_CLASSES, ReadOnlyAPIKeyPermission
from apps.api.v2.discovery.views import PipelineNodesView, PipelineOptionsView
from apps.api.v2.usage.views import UsageView
from apps.api.v2.views import ChatbotViewSet, MeView
from apps.api.views.channels import TriggerBotMessageView
from apps.api.views.experiments import ExperimentViewSet
from apps.api.views.files import FileContentView
from apps.api.views.participants import ParticipantView
from apps.api.views.sessions import ExperimentSessionViewSet
from apps.oauth.permissions import TokenHasOAuthScope

# Every view that accepts API-key authentication, whether it takes the project defaults or sets its
# own ``permission_classes``.
API_KEY_VIEWS = [
    ChatbotViewSet,
    ChatCompletionsView,
    ExperimentSessionViewSet,
    ExperimentViewSet,
    FileContentView,
    MeView,
    ParticipantView,
    PipelineNodesView,
    PipelineOptionsView,
    TriggerBotMessageView,
    UsageView,
]


def _dotted_path(cls) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def test_base_permission_classes_match_project_defaults():
    """BASE_PERMISSION_CLASSES is the non-scope half of the project defaults; views that override
    ``permission_classes`` build on it, so it must not drift from settings."""
    scope_class = _dotted_path(TokenHasOAuthScope)
    defaults = [p for p in settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] if p != scope_class]
    assert [_dotted_path(cls) for cls in BASE_PERMISSION_CLASSES] == defaults


@pytest.mark.parametrize("view", API_KEY_VIEWS, ids=lambda view: view.__name__)
def test_api_key_views_keep_the_read_only_gate(view):
    """Overriding ``permission_classes`` replaces the project defaults, which is how the read-only
    API-key gate (ADR-0021) gets dropped. Every API-key view must still carry it."""
    assert ReadOnlyAPIKeyPermission in view.permission_classes

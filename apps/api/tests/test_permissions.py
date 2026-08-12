from unittest.mock import Mock

import pytest
from django.conf import settings

from apps.api.openai import ChatCompletionsView
from apps.api.permissions import BASE_PERMISSION_CLASSES, ReadOnlyAPIKeyPermission, RequiresTeamPermission
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


def test_chatbots_write_scope_is_declared():
    """TokenHasOAuthResourceScope derives `chatbots:write` for write verbs from
    `required_scopes = ["chatbots"]`; the scope has to exist in settings for a token to carry it."""
    assert "chatbots:write" in settings.OAUTH2_PROVIDER["SCOPES"]


def test_chatbots_write_is_grantable_to_machine_tokens():
    """Machine tokens may build bots headlessly, so the scope is issuable to a
    client-credentials application (APIScopedValidator.validate_scopes gates issuance on this list)."""
    assert "chatbots:write" in settings.OAUTH_CLIENT_CREDENTIALS_SCOPES


class _NeedsChange(RequiresTeamPermission):
    required_permissions = ["experiments.change_experiment"]


def _request(*, has_perms: bool, client_credentials: bool = False):
    request = Mock(user=Mock(has_perms=Mock(return_value=has_perms)))
    request.auth = None if client_credentials else Mock()
    return request


def test_requires_team_permission_allows_a_holder():
    assert _NeedsChange().has_permission(_request(has_perms=True), view=None) is True


def test_requires_team_permission_denies_a_non_holder():
    assert _NeedsChange().has_permission(_request(has_perms=False), view=None) is False


def test_requires_team_permission_defers_machine_tokens_to_the_scope(monkeypatch):
    """A machine token has no user, so there is no membership-derived permission to check;
    authorization rests on the OAuth scope and the token's pinned team."""
    monkeypatch.setattr("apps.api.permissions.is_client_credentials_request", lambda request: True)
    assert _NeedsChange().has_permission(_request(has_perms=False), view=None) is True

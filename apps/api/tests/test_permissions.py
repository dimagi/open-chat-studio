from datetime import timedelta

import pytest
from django.conf import settings
from django.utils import timezone
from rest_framework.request import Request, clone_request

from apps.api.models import UserAPIKey
from apps.api.openai import ChatCompletionsView
from apps.api.permissions import (
    BASE_PERMISSION_CLASSES,
    ApiKeyAuthentication,
    ReadOnlyAPIKeyPermission,
)
from apps.api.v2.discovery.views import (
    PipelineNodeOptionsView,
    PipelineNodesView,
    PipelineNodeView,
    PipelineOptionsView,
)
from apps.api.v2.usage.views import UsageView
from apps.api.v2.views import ChatbotViewSet, MeView
from apps.api.views.channels import TriggerBotMessageView
from apps.api.views.experiments import ExperimentViewSet
from apps.api.views.files import FileContentView
from apps.api.views.participants import ParticipantView
from apps.api.views.sessions import ExperimentSessionViewSet
from apps.oauth.models import OAuth2AccessToken, OAuth2Application
from apps.oauth.permissions import OAuth2AccessTokenAuthentication, TokenHasOAuthScope
from apps.utils.factories.team import TeamWithUsersFactory

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
    PipelineNodeOptionsView,
    PipelineNodesView,
    PipelineNodeView,
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


@pytest.mark.parametrize(
    ("authenticator", "header"),
    [
        pytest.param("api_key", settings.API_KEY_CUSTOM_HEADER, id="api-key"),
        pytest.param("oauth", "HTTP_AUTHORIZATION", id="oauth-token"),
    ],
)
@pytest.mark.django_db()
def test_the_authenticated_team_survives_a_clone_request(rf, authenticator, header):
    """DRF's OPTIONS metadata re-runs the permission checks against a `clone_request`, which wraps
    the same Django request but copies only `_user`/`_auth` off the DRF one. The authenticators
    therefore pin the team on the *Django* request: set on the DRF request alone it would be absent
    here, `request.team` would fall through to the middleware's lazy lookup -- None on an API path
    -- and `DjangoModelPermissions` calls `get_queryset()` with it, so the read would raise."""
    team = TeamWithUsersFactory.create()
    user = team.members.first()

    if authenticator == "api_key":
        _, key = UserAPIKey.objects.create_key(name="k", user=user, team=team)
        credential, authenticate = key, ApiKeyAuthentication().authenticate
    else:
        application = OAuth2Application.objects.create(
            name="auth-code-app",
            team=team,
            client_type=OAuth2Application.CLIENT_CONFIDENTIAL,
            authorization_grant_type=OAuth2Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris="https://example.com/callback",
        )
        token = OAuth2AccessToken.objects.create(
            user=user,
            application=application,
            team=team,
            token="clone-probe-token",
            scope="chatbots:read",
            expires=timezone.now() + timedelta(days=1),
        )
        credential, authenticate = f"Bearer {token.token}", OAuth2AccessTokenAuthentication().authenticate

    request = Request(rf.get("/api/v2/chatbots/", **{header: credential}))
    authenticate(request)

    clone = clone_request(request, "POST")

    assert clone.team == team
    assert clone.team_membership is not None

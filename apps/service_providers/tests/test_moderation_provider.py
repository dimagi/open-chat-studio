from unittest import mock

import openai
import pytest
from django.urls import reverse
from waffle.testutils import override_flag

from apps.service_providers.forms import OpenAIModerationConfigForm
from apps.service_providers.models import ModerationProvider, ModerationProviderType
from apps.service_providers.moderation_service import ModerationRateLimited, OpenAIModerationService
from apps.service_providers.moderation_service.schemas import ModerationOutput
from apps.teams.flags import Flags
from apps.utils.factories.service_provider_factories import ModerationProviderFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.tests.moderation_helpers import DELETED_PROVIDER_NOTIFICATION, select_moderation_provider
from apps.utils.tests.provider_errors import connection_error, status_error

FLAG = Flags.ABUSE_DETECTION.slug
MODERATE = "apps.service_providers.moderation_service.main.OpenAIModerationService.moderate"


def _auth_error(error_cls=openai.AuthenticationError, status_code=401):
    return status_error(
        cls=error_cls,
        status=status_code,
        message=f"Error code: {status_code}",
        body={"message": "Incorrect API key provided"},
    )


@pytest.fixture()
def admin(team_with_users):
    return team_with_users.members.first()


@pytest.fixture()
def admin_client(client, admin):
    client.force_login(admin)
    return client


class TestConfigForm:
    @mock.patch(MODERATE, return_value=ModerationOutput({}))
    def test_valid_credentials_are_accepted(self, moderate):
        form = OpenAIModerationConfigForm(team=None, data={"openai_api_key": "sk-good"})

        assert form.is_valid()
        assert form.warnings == []
        moderate.assert_called_once()

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(_auth_error(), id="authentication"),
            pytest.param(_auth_error(error_cls=openai.PermissionDeniedError, status_code=403), id="permission_denied"),
        ],
    )
    def test_rejected_credentials_are_a_form_error(self, error):
        with mock.patch(MODERATE, side_effect=error):
            form = OpenAIModerationConfigForm(team=None, data={"openai_api_key": "sk-bad"})
            assert not form.is_valid()
        assert form.errors["openai_api_key"] == ["The provider rejected these credentials: Incorrect API key provided"]

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(ModerationRateLimited("Rate limited"), id="rate_limited"),
            pytest.param(connection_error(openai.APIConnectionError), id="connection_error"),
        ],
    )
    def test_other_errors_do_not_block_saving(self, error):
        with mock.patch(MODERATE, side_effect=error):
            form = OpenAIModerationConfigForm(team=None, data={"openai_api_key": "sk-good"})
            assert form.is_valid()
        assert form.warnings == ["Provider saved, but the credentials could not be checked right now."]

    @mock.patch(MODERATE, return_value=ModerationOutput({}))
    def test_check_uses_a_short_timeout_and_no_retries(self, moderate):
        with mock.patch(
            "apps.service_providers.models.OpenAIModerationService", wraps=OpenAIModerationService
        ) as service_cls:
            OpenAIModerationConfigForm(team=None, data={"openai_api_key": "sk-good"}).is_valid()

        kwargs = service_cls.call_args.kwargs
        assert kwargs["max_retries"] == 0
        assert kwargs["timeout"] <= 10

    @pytest.mark.parametrize(
        ("changes", "checked"),
        [
            pytest.param({}, False, id="unchanged"),
            pytest.param({"openai_api_key": "sk-new"}, True, id="api_key"),
            pytest.param({"openai_api_base": "https://example.com/v1"}, True, id="base_url"),
            pytest.param({"openai_organization": "org-2"}, True, id="organization"),
        ],
    )
    def test_edit_checks_only_changed_credentials(self, changes, checked):
        saved = {"openai_api_key": "sk-old", "openai_api_base": "", "openai_organization": "org-1"}
        masked_key = OpenAIModerationConfigForm(team=None, initial=saved).initial["openai_api_key"]
        data = {**saved, "openai_api_key": masked_key, **changes}

        with mock.patch(MODERATE, return_value=ModerationOutput({})) as moderate:
            form = OpenAIModerationConfigForm(team=None, data=data, initial=saved)
            assert form.is_valid()

        assert moderate.called is checked

    def test_field_error_skips_the_check(self):
        with mock.patch(MODERATE) as moderate:
            form = OpenAIModerationConfigForm(team=None, data={"openai_api_key": ""})
            assert not form.is_valid()
        moderate.assert_not_called()


@pytest.mark.django_db()
def test_provider_builds_openai_service():
    provider = ModerationProviderFactory.create(
        config={"openai_api_key": "sk-1", "openai_api_base": "", "openai_organization": "org-1"}
    )

    service = provider.get_moderation_service()

    assert isinstance(service, OpenAIModerationService)
    assert service.openai_api_key.get_secret_value() == "sk-1"
    assert service.openai_api_base is None
    assert service.openai_organization == "org-1"


def _urls(team, provider=None):
    """Moderation provider URLs for ``team``, as ``(http_method, url)``. Edit and delete need ``provider``."""
    kwargs = {"team_slug": team.slug, "provider_type": "moderation"}
    urls = {
        "table": ("get", reverse("service_providers:table", kwargs=kwargs)),
        "new": ("get", reverse("service_providers:new", kwargs={**kwargs, "subtype": "openai"})),
    }
    if provider:
        urls["edit"] = ("get", reverse("service_providers:edit", kwargs={**kwargs, "pk": provider.pk}))
        urls["delete"] = ("delete", reverse("service_providers:delete", kwargs={**kwargs, "pk": provider.pk}))
    return urls


@pytest.mark.django_db()
@pytest.mark.parametrize("view", ["table", "new", "edit", "delete"])
def test_views_are_hidden_with_the_flag_off(view, team_with_users, admin_client):
    provider = ModerationProviderFactory.create(team=team_with_users)
    method, url = _urls(team_with_users, provider)[view]

    with override_flag(FLAG, active=False):
        response = getattr(admin_client, method)(url)

    assert response.status_code == 404
    assert ModerationProvider.objects.filter(id=provider.id).exists()


@pytest.mark.django_db()
@pytest.mark.parametrize("view", ["edit", "delete"])
@override_flag(FLAG, active=True)
def test_views_do_not_reach_another_teams_provider(view, team_with_users, admin_client):
    other_provider = ModerationProviderFactory.create(team=TeamFactory.create())
    method, url = _urls(team_with_users, other_provider)[view]

    response = getattr(admin_client, method)(url)

    assert response.status_code == 404
    assert ModerationProvider.objects.filter(id=other_provider.id).exists()


@pytest.mark.django_db()
@override_flag(FLAG, active=True)
def test_create_view_saves_a_provider(team_with_users, admin_client):
    _, url = _urls(team_with_users)["new"]

    with mock.patch(MODERATE, return_value=ModerationOutput({})):
        response = admin_client.post(url, data={"name": "Main moderation", "openai_api_key": "sk-good"})

    assert response.status_code == 302
    provider = ModerationProvider.objects.get(team=team_with_users)
    assert provider.type == ModerationProviderType.openai
    assert provider.config["openai_api_key"] == "sk-good"


@pytest.mark.django_db()
@override_flag(FLAG, active=True)
def test_create_view_rejects_a_bad_key(team_with_users, admin_client):
    _, url = _urls(team_with_users)["new"]

    with mock.patch(MODERATE, side_effect=_auth_error()):
        response = admin_client.post(url, data={"name": "Main moderation", "openai_api_key": "sk-bad"})

    assert response.status_code == 200
    assert "The provider rejected these credentials: Incorrect API key provided" in response.content.decode()
    assert not ModerationProvider.objects.filter(team=team_with_users).exists()


@pytest.mark.django_db()
@override_flag(FLAG, active=True)
def test_delete_view_turns_moderation_off_and_notifies(
    team_with_users, admin_client, django_capture_on_commit_callbacks
):
    provider = ModerationProviderFactory.create(team=team_with_users, name="Main moderation")
    select_moderation_provider(team=team_with_users, provider=provider)
    method, url = _urls(team_with_users, provider)["delete"]

    with mock.patch(DELETED_PROVIDER_NOTIFICATION) as notify, django_capture_on_commit_callbacks(execute=True):
        response = getattr(admin_client, method)(url)

    assert response.status_code == 200
    assert not ModerationProvider.objects.filter(id=provider.id).exists()
    team_with_users.refresh_from_db()
    assert team_with_users.moderation_enabled is False
    assert team_with_users.moderation_deleted_provider_name == "Main moderation"
    notify.assert_called_once()

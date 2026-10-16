import re
from unittest import mock

import pytest
from django.urls import reverse
from django.utils.http import urlencode
from field_audit.models import AuditAction
from waffle.testutils import override_flag

from apps.moderation.forms import TeamModerationForm
from apps.moderation.models import DeniedParticipantSource
from apps.service_providers.models import ModerationProvider
from apps.service_providers.moderation_service.schemas import ModerationOutput
from apps.teams.backends import CHAT_VIEWER_GROUP, NORMAL_USER_GROUPS, TEAM_ADMIN_GROUP
from apps.teams.flags import Flags
from apps.teams.models import Team
from apps.teams.utils import section_url
from apps.utils.factories.experiment import ParticipantFactory
from apps.utils.factories.moderation import DeniedParticipantFactory
from apps.utils.factories.service_provider_factories import ModerationProviderFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.tests.team_helpers import member_with_groups

FLAG = Flags.ABUSE_DETECTION.slug
MODERATE = "apps.service_providers.moderation_service.main.OpenAIModerationService.moderate"


@pytest.fixture()
def team(team_with_users, team_flag):
    team_flag(flag_name=FLAG, team=team_with_users)
    return team_with_users


@pytest.fixture()
def member(team):
    """Can change participants but is not a team admin."""
    return member_with_groups(team=team, group_names=NORMAL_USER_GROUPS)


@pytest.fixture()
def team_admin_only(team):
    """Team admin without participant permissions."""
    return member_with_groups(team=team, group_names=[TEAM_ADMIN_GROUP])


@pytest.fixture()
def chat_viewer(team):
    return member_with_groups(team=team, group_names=[CHAT_VIEWER_GROUP])


def _section_url(team):
    return section_url(team_slug=team.slug, section_key="moderation")


def _settings_url(team):
    return reverse("moderation:settings", args=[team.slug])


def _table_url(team):
    return reverse("moderation:denied_participants_table", args=[team.slug])


def _new_provider_url(team):
    return reverse("moderation:provider_new", args=[team.slug, "openai"])


@pytest.mark.django_db()
@override_flag(FLAG, active=False)
@pytest.mark.parametrize(
    ("method", "url_builder"),
    [pytest.param("get", _section_url, id="section"), pytest.param("post", _settings_url, id="save_settings")],
)
def test_not_found_with_flag_off(client, team_with_users, admin, method, url_builder):
    client.force_login(admin)

    response = getattr(client, method)(url_builder(team_with_users), {"auto_block_enabled": "on"})

    assert response.status_code == 404
    team_with_users.refresh_from_db()
    assert not team_with_users.auto_block_enabled


@pytest.mark.django_db()
class TestTeamModerationForm:
    @pytest.mark.parametrize(
        ("enabled", "with_provider", "is_valid"),
        [
            pytest.param(True, True, True, id="enabled_with_provider"),
            pytest.param(True, False, False, id="enabled_without_provider"),
            pytest.param(False, False, True, id="disabled_without_provider"),
            pytest.param(False, True, True, id="disabled_with_provider"),
        ],
    )
    def test_enabling_requires_provider(self, enabled, with_provider, is_valid):
        team = TeamFactory.create()
        provider = ModerationProviderFactory.create(team=team)
        data = {"moderation_enabled": enabled, "moderation_provider": provider.pk if with_provider else ""}

        form = TeamModerationForm(data, instance=team)

        assert form.is_valid() is is_valid
        if not is_valid:
            assert "moderation_provider" in form.errors

    def test_rejects_provider_from_another_team(self):
        team = TeamFactory.create()
        other_provider = ModerationProviderFactory.create(team=TeamFactory.create())

        form = TeamModerationForm({"moderation_enabled": True, "moderation_provider": other_provider.pk}, instance=team)

        assert not form.is_valid()
        assert "moderation_provider" in form.errors

    def test_selecting_a_provider_clears_the_deleted_provider_name(self):
        team = TeamFactory.create(moderation_deleted_provider_name="Old provider")
        provider = ModerationProviderFactory.create(team=team)

        form = TeamModerationForm({"moderation_enabled": True, "moderation_provider": provider.pk}, instance=team)
        assert form.is_valid()
        form.save()

        team.refresh_from_db()
        assert team.moderation_enabled
        assert team.moderation_provider == provider
        assert team.moderation_deleted_provider_name == ""

    def test_save_keeps_concurrent_changes_to_other_team_fields(self):
        team = TeamFactory.create(name="Before")
        provider = ModerationProviderFactory.create(team=team)
        form = TeamModerationForm({"moderation_enabled": True, "moderation_provider": provider.pk}, instance=team)
        assert form.is_valid()
        Team.objects.filter(pk=team.pk).update(name="Changed elsewhere", audit_action=AuditAction.IGNORE)

        form.save()

        team.refresh_from_db()
        assert team.name == "Changed elsewhere"
        assert team.moderation_enabled


@pytest.mark.django_db()
class TestSectionVisibility:
    @pytest.mark.parametrize(
        ("user_fixture", "visible"),
        [
            pytest.param("admin", True, id="admin"),
            pytest.param("member", True, id="participant_editor"),
            pytest.param("team_admin_only", True, id="team_admin_only"),
            pytest.param("chat_viewer", False, id="chat_viewer"),
        ],
    )
    def test_visible_to_admins_and_participant_editors(self, client, request, user_fixture, visible, team):
        client.force_login(request.getfixturevalue(user_fixture))

        response = client.get(_section_url(team))

        assert response.status_code == (200 if visible else 404)

    def test_nav_count_is_number_of_blocked_participants(self, client, admin, team):
        DeniedParticipantFactory.create_batch(2, team=team)
        DeniedParticipantFactory.create()
        client.force_login(admin)

        response = client.get(_section_url(team))

        nav_items = {item["key"]: item for item in response.context["nav_sections"]}
        assert nav_items["moderation"]["count"] == 2


@pytest.mark.django_db()
class TestSectionRendering:
    def test_empty_state_without_providers(self, client, admin, team):
        client.force_login(admin)

        response = client.get(_section_url(team))

        content = response.content.decode()
        assert 'data-testid="moderation-empty-state"' in content
        assert "Add moderation provider" in content
        assert 'name="moderation_provider"' not in content

    def test_lists_only_the_teams_providers(self, client, admin, team):
        own = ModerationProviderFactory.create(team=team, name="Own provider")
        ModerationProviderFactory.create(name="Other team provider")
        client.force_login(admin)

        content = client.get(_section_url(team)).content.decode()

        assert own.name in content
        assert "Other team provider" not in content
        assert 'data-testid="edit-moderation-provider"' in content

    def test_deleted_provider_banner(self, client, admin, team):
        team.moderation_deleted_provider_name = "Old provider"
        team.save()
        client.force_login(admin)

        content = client.get(_section_url(team)).content.decode()

        assert 'data-testid="moderation-provider-deleted"' in content
        assert "Old provider" in content

    @pytest.mark.parametrize(
        ("user_fixture", "can_edit", "sees_denylist"),
        [
            pytest.param("admin", True, True, id="admin"),
            pytest.param("member", False, True, id="participant_editor"),
            pytest.param("team_admin_only", True, False, id="team_admin_only"),
        ],
    )
    def test_controls_follow_permissions(self, client, request, team, user_fixture, can_edit, sees_denylist):
        client.force_login(request.getfixturevalue(user_fixture))

        content = client.get(_section_url(team)).content.decode()

        assert ('id="moderation_provider_dialog"' in content) is can_edit
        assert ("denied-participants-table-container" in content) is sees_denylist


@pytest.mark.django_db()
class TestSaveSettings:
    def test_admin_can_turn_moderation_on(self, client, admin, team):
        provider = ModerationProviderFactory.create(team=team)
        client.force_login(admin)

        response = client.post(
            _settings_url(team),
            {"moderation_enabled": "on", "moderation_provider": provider.pk, "auto_block_enabled": "on"},
            headers={"HX-Request": "true"},
        )

        assert response.status_code == 200
        assert response.content.decode().lstrip().startswith('<div class="app-card" id="moderation-settings"')
        team.refresh_from_db()
        assert team.moderation_enabled
        assert team.moderation_provider == provider
        assert team.auto_block_enabled

    def test_invalid_submission_is_not_saved(self, client, admin, team):
        client.force_login(admin)

        response = client.post(_settings_url(team), {"moderation_enabled": "on"})

        assert response.status_code == 200
        assert response.context["moderation_form"].errors
        team.refresh_from_db()
        assert not team.moderation_enabled

    @pytest.mark.parametrize(
        "user_fixture",
        [pytest.param("member", id="participant_editor"), pytest.param("chat_viewer", id="chat_viewer")],
    )
    def test_requires_team_admin(self, client, request, team, user_fixture):
        client.force_login(request.getfixturevalue(user_fixture))

        response = client.post(_settings_url(team), {"auto_block_enabled": "on"})

        assert response.status_code == 403
        team.refresh_from_db()
        assert not team.auto_block_enabled


@pytest.mark.django_db()
class TestProviderDialog:
    def test_get_renders_provider_form(self, client, admin, team):
        client.force_login(admin)

        response = client.get(_new_provider_url(team))

        content = response.content.decode()
        assert response.status_code == 200
        assert 'name="name"' in content
        assert 'name="openai_api_key"' in content
        assert "Create and select" in content

    @mock.patch(MODERATE, return_value=ModerationOutput({}))
    def test_create_and_select(self, _moderate, client, admin, team):
        client.force_login(admin)

        response = client.post(
            _new_provider_url(team),
            {"name": "My moderation", "openai_api_key": "sk-test", "auto_block_enabled": "on"},
        )

        provider = ModerationProvider.objects.get(team=team, name="My moderation")
        assert response.status_code == 200
        assert response.headers["HX-Retarget"] == "#moderation-settings"
        assert "moderation-provider-saved" in response.headers["HX-Trigger"]
        assert re.search(rf'<option value="{provider.pk}"\s+selected>', response.content.decode())
        assert response.context["moderation_form"]["auto_block_enabled"].value() is True
        team.refresh_from_db()
        assert team.moderation_provider is None

    def test_invalid_form_rerenders_dialog(self, client, admin, team):
        client.force_login(admin)

        response = client.post(_new_provider_url(team), {"name": "", "openai_api_key": ""})

        assert response.status_code == 200
        assert "HX-Retarget" not in response.headers
        assert not ModerationProvider.objects.filter(team=team).exists()

    @mock.patch(MODERATE, return_value=ModerationOutput({}))
    def test_edit_selected_provider(self, _moderate, client, admin, team):
        provider = ModerationProviderFactory.create(team=team, name="Before")
        client.force_login(admin)
        url = reverse("moderation:provider_edit", args=[team.slug, provider.pk])

        response = client.post(url, {"name": "After", "openai_api_key": "sk-new"})

        assert response.status_code == 200
        provider.refresh_from_db()
        assert provider.name == "After"
        assert response.context["moderation_form"]["moderation_provider"].value() == provider.pk

    def test_cannot_edit_another_teams_provider(self, client, admin, team):
        other = ModerationProviderFactory.create()
        client.force_login(admin)

        response = client.get(reverse("moderation:provider_edit", args=[team.slug, other.pk]))

        assert response.status_code == 404


@pytest.mark.django_db()
class TestDenylistTable:
    def test_lists_team_denials(self, client, admin, member, team):
        participant = ParticipantFactory.create(team=team, name="Blocked one", platform="web")
        DeniedParticipantFactory.create(team=team, participant=participant, reason="Spam", created_by=member)
        DeniedParticipantFactory.create(team=team, source=DeniedParticipantSource.AUTO)
        DeniedParticipantFactory.create(team=team, source=DeniedParticipantSource.CODE_NODE)
        DeniedParticipantFactory.create(reason="Other team")
        client.force_login(admin)

        response = client.get(_table_url(team))

        content = response.content.decode()
        assert "Blocked one" in content
        assert "Spam" in content
        assert member.get_display_name() in content
        assert "Auto-block" in content
        assert "Pipeline" in content
        assert "Other team" not in content
        assert reverse("participants:unblock_participant", args=[team.slug, participant.pk]) in content
        assert "confirmed" in content
        assert urlencode({"next": _section_url(team)}) in content

    @pytest.mark.parametrize(
        ("user_fixture", "status"),
        [
            pytest.param("member", 200, id="participant_editor"),
            pytest.param("team_admin_only", 403, id="team_admin_only"),
            pytest.param("chat_viewer", 403, id="chat_viewer"),
        ],
    )
    def test_requires_change_participant(self, client, request, team, user_fixture, status):
        client.force_login(request.getfixturevalue(user_fixture))

        response = client.get(_table_url(team))

        assert response.status_code == status

    def test_query_count_does_not_grow_with_rows(self, client, admin, team, django_assert_max_num_queries):
        DeniedParticipantFactory.create(team=team, created_by=admin)
        client.force_login(admin)
        htmx = {"HX-Request": "true"}
        client.get(_table_url(team), headers=htmx)
        with django_assert_max_num_queries(100) as one_row:
            client.get(_table_url(team), headers=htmx)

        DeniedParticipantFactory.create_batch(5, team=team, created_by=admin)
        with django_assert_max_num_queries(len(one_row.captured_queries)):
            client.get(_table_url(team), headers=htmx)

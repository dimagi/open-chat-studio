from unittest import mock

import pytest
from field_audit.models import AuditAction

from apps.service_providers.models import ModerationProvider
from apps.teams.models import Team
from apps.utils.factories.service_provider_factories import ModerationProviderFactory

NOTIFY = "apps.moderation.signals.moderation_provider_deleted_notification"


def _select(team, provider):
    team.moderation_enabled = True
    team.moderation_provider = provider
    team.save()


@pytest.fixture()
def selected_provider(db):
    provider = ModerationProviderFactory.create(name="Main moderation")
    _select(provider.team, provider)
    return provider


def _delete_instance(provider):
    provider.delete()


def _delete_queryset(provider):
    ModerationProvider.objects.filter(id=provider.id).delete(audit_action=AuditAction.AUDIT)


@pytest.mark.parametrize(
    "delete",
    [pytest.param(_delete_instance, id="instance"), pytest.param(_delete_queryset, id="queryset")],
)
def test_deleting_the_selected_provider_turns_moderation_off_and_notifies(
    delete, selected_provider, django_capture_on_commit_callbacks
):
    team = selected_provider.team
    provider_id = selected_provider.id

    with mock.patch(NOTIFY) as notify, django_capture_on_commit_callbacks(execute=True):
        delete(selected_provider)

    team.refresh_from_db()
    assert team.moderation_enabled is False
    assert team.moderation_provider is None
    assert team.moderation_deleted_provider_name == "Main moderation"
    notify.assert_called_once_with(team=team, provider_id=provider_id, provider_name="Main moderation")


def test_deleting_another_provider_leaves_moderation_on(selected_provider, django_capture_on_commit_callbacks):
    team = selected_provider.team
    other = ModerationProviderFactory.create(team=team)

    with mock.patch(NOTIFY) as notify, django_capture_on_commit_callbacks(execute=True):
        other.delete()

    team.refresh_from_db()
    assert team.moderation_enabled is True
    assert team.moderation_provider == selected_provider
    assert team.moderation_deleted_provider_name == ""
    notify.assert_not_called()


def test_deleting_the_team_does_not_notify(selected_provider, django_capture_on_commit_callbacks):
    team = selected_provider.team

    with mock.patch(NOTIFY) as notify, django_capture_on_commit_callbacks(execute=True):
        team.delete()

    assert not Team.objects.filter(id=team.id).exists()
    assert not ModerationProvider.objects.filter(id=selected_provider.id).exists()
    notify.assert_not_called()

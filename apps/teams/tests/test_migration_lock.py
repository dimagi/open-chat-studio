import pytest
from django.urls import reverse
from field_audit import enable_audit
from field_audit.models import AuditEvent

from apps.teams.models import Team
from apps.utils.factories.team import TeamFactory, TeamWithUsersFactory


@pytest.mark.django_db()
def test_is_migrating_defaults_to_false():
    assert TeamFactory().is_migrating is False


@pytest.mark.django_db()
def test_toggling_is_migrating_is_audited():
    with enable_audit():
        team = TeamFactory()
        team.is_migrating = True
        team.save()
        events = AuditEvent.objects.by_model(Team).filter(object_pk=team.id)
        assert any("is_migrating" in (e.delta or {}) for e in events)


def _team_with_admin():
    team = TeamWithUsersFactory()
    admin = next(m.user for m in team.membership_set.all() if m.is_team_admin())
    return team, admin


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("is_admin", "expected_status", "expected_is_migrating"),
    [
        pytest.param(True, 200, True, id="admin_can_arm"),
        pytest.param(False, 403, False, id="non_admin_cannot_arm"),
    ],
)
def test_arm_migration_lock(client, is_admin, expected_status, expected_is_migrating):
    team, admin = _team_with_admin()
    if is_admin:
        user = admin
    else:
        user = next(m.user for m in team.membership_set.all() if not m.is_team_admin())
    client.force_login(user)

    response = client.post(reverse("single_team:set_migration_lock", args=[team.slug]), {"is_migrating": "on"})

    assert response.status_code == expected_status
    team.refresh_from_db()
    assert team.is_migrating is expected_is_migrating


@pytest.mark.django_db()
def test_admin_can_clear_migration_lock(client):
    team, admin = _team_with_admin()
    client.force_login(admin)
    url = reverse("single_team:set_migration_lock", args=[team.slug])
    client.post(url, {"is_migrating": "on"})

    client.post(url, {})  # unchecked checkbox -> cleared

    team.refresh_from_db()
    assert team.is_migrating is False

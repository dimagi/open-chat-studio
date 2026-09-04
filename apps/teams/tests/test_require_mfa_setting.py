import pytest
from allauth.mfa.totp.internal import auth as totp_auth
from django.urls import reverse
from field_audit import enable_audit
from field_audit.models import AuditEvent

from apps.teams.models import Team
from apps.utils.factories.team import TeamFactory, TeamWithUsersFactory


@pytest.mark.django_db()
def test_require_mfa_defaults_to_false():
    assert TeamFactory().require_mfa is False


@pytest.mark.django_db()
def test_toggling_require_mfa_is_audited():
    with enable_audit():
        team = TeamFactory()
        team.require_mfa = True
        team.save()
        events = AuditEvent.objects.by_model(Team).filter(object_pk=team.id)
        assert any("require_mfa" in (e.delta or {}) for e in events)


def _team_with_admin():
    team = TeamWithUsersFactory()
    admin = next(m.user for m in team.membership_set.all() if m.is_team_admin())
    return team, admin


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("is_admin", "expected_status", "expected_require_mfa"),
    [
        pytest.param(True, 200, True, id="admin_can_enable"),
        pytest.param(False, 403, False, id="non_admin_cannot_enable"),
    ],
)
def test_set_require_mfa(client, is_admin, expected_status, expected_require_mfa):
    team, admin = _team_with_admin()
    if is_admin:
        user = admin
    else:
        user = next(m.user for m in team.membership_set.all() if not m.is_team_admin())
    client.force_login(user)

    response = client.post(reverse("single_team:set_require_mfa", args=[team.slug]), {"require_mfa": "on"})

    assert response.status_code == expected_status
    team.refresh_from_db()
    assert team.require_mfa is expected_require_mfa


@pytest.mark.django_db()
def test_admin_can_disable_require_mfa(client):
    """Enrolled first: an admin who isn't enrolled gets sent to MFA setup by their own
    requirement on the very next request -- see test_require_mfa_middleware.py. This test
    isolates the toggle-off capability from that gate, not a workaround for it."""
    team, admin = _team_with_admin()
    totp_auth.TOTP.activate(admin, totp_auth.generate_totp_secret())
    client.force_login(admin)
    url = reverse("single_team:set_require_mfa", args=[team.slug])
    client.post(url, {"require_mfa": "on"})

    client.post(url, {})  # unchecked checkbox -> cleared

    team.refresh_from_db()
    assert team.require_mfa is False

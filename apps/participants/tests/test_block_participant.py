import pytest
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.urls import reverse
from field_audit import enable_audit
from field_audit.models import AuditEvent
from waffle.testutils import override_flag

from apps.moderation.models import DeniedParticipant, DeniedParticipantSource
from apps.teams.backends import get_team_owner_groups
from apps.teams.flags import Flags
from apps.utils.factories.experiment import ParticipantFactory
from apps.utils.factories.moderation import DeniedParticipantFactory
from apps.utils.factories.team import MembershipFactory, TeamFactory, UserFactory

FLAG = Flags.ABUSE_DETECTION.slug


def _block_url(team, participant):
    return reverse("participants:block_participant", args=[team.slug, participant.id])


def _unblock_url(team, participant):
    return reverse("participants:unblock_participant", args=[team.slug, participant.id])


def _page_url(team, participant):
    return reverse("participants:single-participant-home", args=[team.slug, participant.id])


@pytest.fixture()
def admin(team_with_users):
    return team_with_users.members.first()


@pytest.fixture()
def participant(team_with_users):
    return ParticipantFactory.create(team=team_with_users)


@pytest.fixture()
def viewer(team_with_users):
    """A team member who can view participants but not change them."""
    user = UserFactory()
    MembershipFactory.create(user=user, team=team_with_users)
    user.user_permissions.add(Permission.objects.get(codename="view_participant"))
    return user


@pytest.mark.django_db()
class TestBlockParticipant:
    def test_creates_manual_denial_with_flag_on(self, client, team_with_users, team_flag, admin, participant):
        team_flag(flag_name=FLAG, team=team_with_users)
        client.force_login(admin)

        response = client.post(_block_url(team=team_with_users, participant=participant), {"reason": "Spamming links"})

        assert response.status_code == 302
        assert response.url == _page_url(team=team_with_users, participant=participant)
        denial = DeniedParticipant.objects.get(team=team_with_users, participant=participant)
        assert denial.source == DeniedParticipantSource.MANUAL
        assert denial.reason == "Spamming links"
        assert denial.created_by == admin

    def test_reason_is_optional(self, client, team_with_users, team_flag, admin, participant):
        team_flag(flag_name=FLAG, team=team_with_users)
        client.force_login(admin)

        client.post(_block_url(team=team_with_users, participant=participant), {})

        assert DeniedParticipant.objects.get(participant=participant).reason == ""

    def test_invalid_reason_does_not_block(self, client, team_with_users, team_flag, admin, participant):
        team_flag(flag_name=FLAG, team=team_with_users)
        client.force_login(admin)

        response = client.post(_block_url(team=team_with_users, participant=participant), {"reason": "spam\x00"})

        assert response.url == _page_url(team=team_with_users, participant=participant)
        assert not DeniedParticipant.objects.filter(participant=participant).exists()
        [message] = get_messages(response.wsgi_request)
        assert message.level_tag == "error"

    def test_already_blocked_keeps_the_existing_denial(self, client, team_with_users, team_flag, admin, participant):
        team_flag(flag_name=FLAG, team=team_with_users)
        existing = DeniedParticipantFactory.create(
            team=team_with_users, participant=participant, source=DeniedParticipantSource.AUTO, reason="Abuse"
        )
        client.force_login(admin)

        response = client.post(_block_url(team=team_with_users, participant=participant), {"reason": "Another reason"})

        assert response.status_code == 302
        assert list(DeniedParticipant.objects.filter(participant=participant)) == [existing]
        existing.refresh_from_db()
        assert existing.source == DeniedParticipantSource.AUTO
        assert existing.reason == "Abuse"

    @override_flag(FLAG, active=False)
    def test_not_found_with_flag_off(self, client, team_with_users, admin, participant):
        client.force_login(admin)

        response = client.post(_block_url(team=team_with_users, participant=participant), {"reason": "Spam"})

        assert response.status_code == 404
        assert not DeniedParticipant.objects.filter(participant=participant).exists()

    def test_requires_change_permission(self, client, team_with_users, team_flag, viewer, participant):
        team_flag(flag_name=FLAG, team=team_with_users)
        client.force_login(viewer)

        response = client.post(_block_url(team=team_with_users, participant=participant), {"reason": "Spam"})

        assert response.status_code == 302
        assert response.url.startswith(reverse("account_login"))
        assert not DeniedParticipant.objects.filter(participant=participant).exists()

    def test_cannot_block_another_teams_participant(self, client, team_with_users, team_flag, participant):
        other_team = TeamFactory.create()
        team_flag(flag_name=FLAG, team=other_team)
        outsider = UserFactory()
        # An admin of their own team. What stops them here is team scoping, not permissions.
        MembershipFactory.create(user=outsider, team=other_team, groups=get_team_owner_groups)
        client.force_login(outsider)

        response = client.post(_block_url(team=other_team, participant=participant), {"reason": "Spam"})

        assert response.status_code == 404
        assert not DeniedParticipant.objects.filter(participant=participant).exists()

    def test_cannot_block_a_team_member(self, client, team_with_users, team_flag, admin):
        team_flag(flag_name=FLAG, team=team_with_users)
        member = team_with_users.members.last()
        participant = ParticipantFactory.create(team=team_with_users, user=member)
        client.force_login(admin)

        response = client.post(_block_url(team=team_with_users, participant=participant), {"reason": "Spam"})

        assert response.status_code == 302
        assert response.url == _page_url(team=team_with_users, participant=participant)
        assert not DeniedParticipant.objects.filter(participant=participant).exists()
        assert [str(m) for m in get_messages(response.wsgi_request)] == ["Team members cannot be blocked"]

    def test_can_block_a_user_from_another_team(self, client, team_with_users, team_flag, admin):
        team_flag(flag_name=FLAG, team=team_with_users)
        outsider = UserFactory()
        MembershipFactory.create(user=outsider, team=TeamFactory.create())
        participant = ParticipantFactory.create(team=team_with_users, user=outsider)
        client.force_login(admin)

        client.post(_block_url(team=team_with_users, participant=participant), {"reason": "Spam"})

        assert DeniedParticipant.objects.filter(participant=participant).exists()

    def test_get_not_allowed(self, client, team_with_users, team_flag, admin, participant):
        team_flag(flag_name=FLAG, team=team_with_users)
        client.force_login(admin)

        response = client.get(_block_url(team=team_with_users, participant=participant))

        assert response.status_code == 405

    def test_writes_audit_event(self, client, team_with_users, team_flag, admin, participant):
        team_flag(flag_name=FLAG, team=team_with_users)
        client.force_login(admin)

        with enable_audit():
            client.post(_block_url(team=team_with_users, participant=participant), {"reason": "Spamming links"})

        denial = DeniedParticipant.objects.get(participant=participant)
        event = AuditEvent.objects.by_model(DeniedParticipant).get(object_pk=denial.pk)
        assert event.is_create
        assert event.delta["reason"]["new"] == "Spamming links"
        assert event.change_context["username"] == admin.username


@pytest.mark.django_db()
class TestUnblockParticipant:
    def test_deletes_denial_with_flag_on(self, client, team_with_users, team_flag, admin, participant):
        team_flag(flag_name=FLAG, team=team_with_users)
        DeniedParticipantFactory.create(team=team_with_users, participant=participant)
        client.force_login(admin)

        response = client.post(_unblock_url(team=team_with_users, participant=participant))

        assert response.headers["HX-Redirect"] == _page_url(team=team_with_users, participant=participant)
        assert not DeniedParticipant.objects.filter(participant=participant).exists()

    def test_not_blocked_redirects_back(self, client, team_with_users, team_flag, admin, participant):
        team_flag(flag_name=FLAG, team=team_with_users)
        client.force_login(admin)

        response = client.post(_unblock_url(team=team_with_users, participant=participant))

        assert response.headers["HX-Redirect"] == _page_url(team=team_with_users, participant=participant)

    @override_flag(FLAG, active=False)
    def test_not_found_with_flag_off(self, client, team_with_users, admin, participant):
        DeniedParticipantFactory.create(team=team_with_users, participant=participant)
        client.force_login(admin)

        response = client.post(_unblock_url(team=team_with_users, participant=participant))

        assert response.status_code == 404
        assert DeniedParticipant.objects.filter(participant=participant).exists()

    def test_requires_change_permission(self, client, team_with_users, team_flag, viewer, participant):
        team_flag(flag_name=FLAG, team=team_with_users)
        DeniedParticipantFactory.create(team=team_with_users, participant=participant)
        client.force_login(viewer)

        response = client.post(_unblock_url(team=team_with_users, participant=participant))

        assert response.status_code == 302
        assert response.url.startswith(reverse("account_login"))
        assert DeniedParticipant.objects.filter(participant=participant).exists()

    def test_cannot_unblock_another_teams_participant(self, client, team_with_users, team_flag, participant):
        DeniedParticipantFactory.create(team=team_with_users, participant=participant)
        other_team = TeamFactory.create()
        team_flag(flag_name=FLAG, team=other_team)
        outsider = UserFactory()
        MembershipFactory.create(user=outsider, team=other_team, groups=get_team_owner_groups)
        client.force_login(outsider)

        response = client.post(_unblock_url(team=other_team, participant=participant))

        assert response.status_code == 404
        assert DeniedParticipant.objects.filter(participant=participant).exists()

    def test_writes_audit_event(self, client, team_with_users, team_flag, admin, participant):
        team_flag(flag_name=FLAG, team=team_with_users)
        denial = DeniedParticipantFactory.create(team=team_with_users, participant=participant, reason="Spam")
        client.force_login(admin)

        with enable_audit():
            client.post(_unblock_url(team=team_with_users, participant=participant))

        event = AuditEvent.objects.by_model(DeniedParticipant).get(object_pk=denial.pk)
        assert event.is_delete
        assert event.delta["reason"]["old"] == "Spam"
        assert event.change_context["username"] == admin.username


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("url_builder", "already_blocked"),
    [
        pytest.param(_block_url, False, id="block"),
        pytest.param(_block_url, True, id="block-already-blocked"),
        pytest.param(_unblock_url, True, id="unblock"),
        pytest.param(_unblock_url, False, id="unblock-not-blocked"),
    ],
)
def test_flash_message_escapes_participant_name(
    client, team_with_users, team_flag, admin, url_builder, already_blocked
):
    """Flash messages are rendered as HTML, and participants can set their own name."""
    team_flag(flag_name=FLAG, team=team_with_users)
    participant = ParticipantFactory.create(team=team_with_users, name="<b>x</b>")
    if already_blocked:
        DeniedParticipantFactory.create(team=team_with_users, participant=participant)
    client.force_login(admin)

    response = client.post(url_builder(team=team_with_users, participant=participant))

    [message] = get_messages(response.wsgi_request)
    assert "&lt;b&gt;x&lt;/b&gt;" in str(message)
    assert "<b>" not in str(message)


@pytest.mark.django_db()
class TestParticipantPageBlockControls:
    def test_shows_block_button_with_flag_on(self, client, team_with_users, team_flag, admin, participant):
        team_flag(flag_name=FLAG, team=team_with_users)
        client.force_login(admin)

        content = client.get(_page_url(team=team_with_users, participant=participant)).content.decode()

        assert _block_url(team=team_with_users, participant=participant) in content
        assert _unblock_url(team=team_with_users, participant=participant) not in content

    def test_shows_blocked_status_and_unblock_button_with_flag_on(
        self, client, team_with_users, team_flag, admin, participant
    ):
        team_flag(flag_name=FLAG, team=team_with_users)
        DeniedParticipantFactory.create(
            team=team_with_users,
            participant=participant,
            source=DeniedParticipantSource.MANUAL,
            reason="Spamming links",
            created_by=admin,
        )
        client.force_login(admin)

        response = client.get(_page_url(team=team_with_users, participant=participant))

        content = response.content.decode()
        assert response.context["denial"].reason == "Spamming links"
        assert "Blocked" in content
        assert "Spamming links" in content
        assert _unblock_url(team=team_with_users, participant=participant) in content
        assert _block_url(team=team_with_users, participant=participant) not in content

    def test_disables_block_button_for_a_team_member_with_flag_on(self, client, team_with_users, team_flag, admin):
        team_flag(flag_name=FLAG, team=team_with_users)
        participant = ParticipantFactory.create(team=team_with_users, user=admin)
        client.force_login(admin)

        response = client.get(_page_url(team=team_with_users, participant=participant))

        assert response.context["can_block"] is False
        assert _block_url(team=team_with_users, participant=participant) not in response.content.decode()

    def test_shows_unblock_button_for_a_blocked_team_member_with_flag_on(
        self, client, team_with_users, team_flag, admin
    ):
        """A participant blocked before their user joined the team can still be unblocked."""
        team_flag(flag_name=FLAG, team=team_with_users)
        participant = ParticipantFactory.create(team=team_with_users, user=admin)
        DeniedParticipantFactory.create(team=team_with_users, participant=participant)
        client.force_login(admin)

        content = client.get(_page_url(team=team_with_users, participant=participant)).content.decode()

        assert _unblock_url(team=team_with_users, participant=participant) in content

    @override_flag(FLAG, active=False)
    def test_hides_block_controls_with_flag_off(self, client, team_with_users, admin, participant):
        DeniedParticipantFactory.create(team=team_with_users, participant=participant, reason="Spamming links")
        client.force_login(admin)

        response = client.get(_page_url(team=team_with_users, participant=participant))

        content = response.content.decode()
        assert response.context.get("denial") is None
        assert "Spamming links" not in content
        assert _block_url(team=team_with_users, participant=participant) not in content
        assert _unblock_url(team=team_with_users, participant=participant) not in content

    def test_viewer_sees_blocked_status_without_buttons_with_flag_on(
        self, client, team_with_users, team_flag, viewer, participant
    ):
        team_flag(flag_name=FLAG, team=team_with_users)
        DeniedParticipantFactory.create(team=team_with_users, participant=participant, reason="Spamming links")
        client.force_login(viewer)

        content = client.get(_page_url(team=team_with_users, participant=participant)).content.decode()

        assert "Spamming links" in content
        assert _unblock_url(team=team_with_users, participant=participant) not in content

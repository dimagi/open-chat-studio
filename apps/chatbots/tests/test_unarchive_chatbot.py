import pytest
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.urls import reverse

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chatbots.version_resolver import resolve_published_or_working
from apps.events.models import EventActionType, ScheduledMessage, TimePeriod
from apps.pipelines.models import Node
from apps.teams.backends import get_team_owner_groups
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.events import EventActionFactory, ScheduledMessageFactory, StaticTriggerFactory
from apps.utils.factories.experiment import ExperimentFactory, ParticipantFactory
from apps.utils.factories.team import MembershipFactory, TeamFactory, UserFactory


def _unarchive_url(team, chatbot):
    return reverse("chatbots:unarchive", args=[team.slug, chatbot.id])


TELEGRAM_TOKEN = "token-123"


def _claim_telegram_token(team, token=TELEGRAM_TOKEN):
    """Give the token to another live chatbot, as a team may do while the owner sits archived."""
    claimant = ExperimentFactory.create(team=team, owner=team.members.first())
    return ExperimentChannelFactory.create(
        team=team, experiment=claimant, platform=ChannelPlatform.TELEGRAM, extra_data={"bot_token": token}
    )


def _messages(response):
    return " ".join(str(message) for message in get_messages(response.wsgi_request))


@pytest.fixture()
def archived_chatbot(team_with_users):
    """An archived chatbot with one published version."""
    owner = team_with_users.members.first()
    chatbot = ExperimentFactory.create(team=team_with_users, owner=owner)
    version = chatbot.create_new_version(make_default=True)
    chatbot.archive()
    chatbot.refresh_from_db()
    version.refresh_from_db()
    return chatbot, version


@pytest.mark.django_db()
class TestUnarchiveChatbot:
    def test_restores_the_chatbot(self, client, team_with_users, archived_chatbot):
        chatbot, _version = archived_chatbot
        client.force_login(team_with_users.members.first())

        response = client.post(_unarchive_url(team_with_users, chatbot))

        assert response.status_code == 302
        chatbot.refresh_from_db()
        assert chatbot.is_editable is True

    def test_restores_the_versions(self, client, team_with_users, archived_chatbot):
        chatbot, version = archived_chatbot
        assert version.is_archived is True
        client.force_login(team_with_users.members.first())

        client.post(_unarchive_url(team_with_users, chatbot))

        version.refresh_from_db()
        assert version.is_archived is False

    def test_restores_the_static_triggers(self, client, team_with_users):
        owner = team_with_users.members.first()
        chatbot = ExperimentFactory.create(team=team_with_users, owner=owner)
        trigger = StaticTriggerFactory.create(experiment=chatbot)
        chatbot.archive()
        trigger.refresh_from_db()
        assert trigger.is_archived is True
        client.force_login(owner)

        client.post(_unarchive_url(team_with_users, chatbot))

        trigger.refresh_from_db()
        assert trigger.is_archived is False

    def test_version_archived_on_its_own_comes_back_too(self, client, team_with_users):
        """archive() cannot tell a deliberately retired version from collateral, so both return —
        and a version retired on its own also has its pipeline, nodes and triggers to restore."""
        owner = team_with_users.members.first()
        chatbot = ExperimentFactory.create(team=team_with_users, owner=owner)
        retired = chatbot.create_new_version()
        trigger = StaticTriggerFactory.create(experiment=retired)
        retired.archive()
        nodes = Node.objects.get_all().filter(pipeline=retired.pipeline)
        assert nodes.filter(is_archived=True).exists()
        chatbot.archive()
        client.force_login(owner)

        client.post(_unarchive_url(team_with_users, chatbot))

        retired.refresh_from_db()
        retired.pipeline.refresh_from_db()
        trigger.refresh_from_db()
        assert retired.is_archived is False
        assert retired.pipeline.is_archived is False
        assert not nodes.filter(is_archived=True).exists()
        assert trigger.is_archived is False

    def test_serves_the_default_version_again(self, client, team_with_users, archived_chatbot):
        """Leaving the default version archived would silently serve the unpublished working version."""
        chatbot, version = archived_chatbot
        client.force_login(team_with_users.members.first())

        client.post(_unarchive_url(team_with_users, chatbot))

        chatbot.refresh_from_db()
        assert resolve_published_or_working(chatbot) == version

    def test_scheduled_messages_are_not_recreated(self, client, team_with_users):
        owner = team_with_users.members.first()
        chatbot = ExperimentFactory.create(team=team_with_users, owner=owner)
        action = EventActionFactory.create(
            action_type=EventActionType.SCHEDULETRIGGER,
            params={"name": "Reminder", "time_period": TimePeriod.DAYS, "frequency": 1, "repetitions": 1},
        )
        ScheduledMessageFactory.create(
            team=team_with_users,
            experiment=chatbot,
            participant=ParticipantFactory.create(team=team_with_users),
            action=action,
        )
        chatbot.archive()
        assert not ScheduledMessage.objects.filter(experiment=chatbot).exists()
        client.force_login(owner)

        client.post(_unarchive_url(team_with_users, chatbot))

        assert not ScheduledMessage.objects.filter(experiment=chatbot).exists()

    def test_get_is_rejected(self, client, team_with_users, archived_chatbot):
        chatbot, _version = archived_chatbot
        client.force_login(team_with_users.members.first())

        response = client.get(_unarchive_url(team_with_users, chatbot))

        assert response.status_code == 405

    def test_an_active_chatbot_is_left_alone(self, client, team_with_users):
        """Unarchiving a live chatbot would reactivate records archived for other reasons."""
        owner = team_with_users.members.first()
        chatbot = ExperimentFactory.create(team=team_with_users, owner=owner)
        retired = chatbot.create_new_version()
        retired.archive()
        client.force_login(owner)

        response = client.post(_unarchive_url(team_with_users, chatbot))

        assert response.status_code == 404
        retired.refresh_from_db()
        assert retired.is_archived is True

    def test_other_team_cannot_unarchive(self, client, team_with_users, archived_chatbot):
        chatbot, _version = archived_chatbot
        outsider = UserFactory()
        other_team = TeamFactory()
        MembershipFactory.create(user=outsider, team=other_team, groups=get_team_owner_groups)
        client.force_login(outsider)

        response = client.post(_unarchive_url(other_team, chatbot))

        assert response.status_code == 404
        chatbot.refresh_from_db()
        assert chatbot.is_archived is True

    def test_requires_change_permission(self, client, team_with_users, archived_chatbot):
        chatbot, _version = archived_chatbot
        viewer = UserFactory()
        MembershipFactory.create(user=viewer, team=team_with_users)
        viewer.user_permissions.add(Permission.objects.get(codename="view_experiment"))
        client.force_login(viewer)

        response = client.post(_unarchive_url(team_with_users, chatbot))

        assert response.status_code == 403
        chatbot.refresh_from_db()
        assert chatbot.is_archived is True

    def test_page_offers_the_action(self, client, team_with_users, archived_chatbot):
        """The dialog is the only route to unarchiving. Without it there is no feature."""
        chatbot, _version = archived_chatbot
        client.force_login(team_with_users.members.first())

        response = client.get(reverse("chatbots:single_chatbot_home", args=[team_with_users.slug, chatbot.id]))

        page = response.content.decode()
        assert _unarchive_url(team_with_users, chatbot) in page
        assert 'name="restore_channels"' in page
        assert "Scheduled messages were deleted" in page

    def test_a_live_chatbot_does_not_offer_the_action(self, client, team_with_users):
        chatbot = ExperimentFactory.create(team=team_with_users, owner=team_with_users.members.first())
        client.force_login(team_with_users.members.first())

        response = client.get(reverse("chatbots:single_chatbot_home", args=[team_with_users.slug, chatbot.id]))

        assert _unarchive_url(team_with_users, chatbot) not in response.content.decode()

    def test_a_viewer_is_not_offered_the_action(self, client, team_with_users, archived_chatbot):
        chatbot, _version = archived_chatbot
        viewer = UserFactory()
        MembershipFactory.create(user=viewer, team=team_with_users)
        viewer.user_permissions.add(Permission.objects.get(codename="view_experiment"))
        client.force_login(viewer)

        response = client.get(reverse("chatbots:single_chatbot_home", args=[team_with_users.slug, chatbot.id]))

        assert _unarchive_url(team_with_users, chatbot) not in response.content.decode()


@pytest.mark.django_db()
class TestUnarchiveChatbotChannels:
    @pytest.fixture()
    def chatbot_with_channel(self, team_with_users):
        owner = team_with_users.members.first()
        chatbot = ExperimentFactory.create(team=team_with_users, owner=owner)
        channel = ExperimentChannelFactory.create(
            team=team_with_users,
            experiment=chatbot,
            platform=ChannelPlatform.TELEGRAM,
            extra_data={"bot_token": TELEGRAM_TOKEN},
        )
        chatbot.archive()
        channel.refresh_from_db()
        assert channel.deleted is True
        return chatbot, channel

    def test_channels_stay_disconnected_by_default(self, client, team_with_users, chatbot_with_channel):
        """The checkbox is opt-in: an unticked restore leaves every channel alone."""
        chatbot, channel = chatbot_with_channel
        client.force_login(team_with_users.members.first())

        client.post(_unarchive_url(team_with_users, chatbot))

        channel.refresh_from_db()
        assert channel.deleted is True

    def test_restores_a_channel_whose_identifier_is_free(self, client, team_with_users, chatbot_with_channel):
        chatbot, channel = chatbot_with_channel
        client.force_login(team_with_users.members.first())

        client.post(_unarchive_url(team_with_users, chatbot), {"restore_channels": "on"})

        channel.refresh_from_db()
        assert channel.deleted is False

    @pytest.mark.parametrize(
        ("platform", "extra_data", "claim_the_token"),
        [
            pytest.param(
                ChannelPlatform.TELEGRAM,
                {"bot_token": TELEGRAM_TOKEN},
                True,
                id="identifier-claimed-while-archived",
            ),
            pytest.param(
                ChannelPlatform.SLACK,
                {"slack_channel_id": "C123", "slack_team_id": "T123"},
                False,
                id="slack-conflict-rules-live-in-the-form",
            ),
        ],
    )
    def test_skips_and_reports_a_channel_it_cannot_restore(
        self, client, team_with_users, platform, extra_data, claim_the_token
    ):
        owner = team_with_users.members.first()
        chatbot = ExperimentFactory.create(team=team_with_users, owner=owner)
        channel = ExperimentChannelFactory.create(
            team=team_with_users, experiment=chatbot, platform=platform, extra_data=extra_data
        )
        chatbot.archive()
        if claim_the_token:
            _claim_telegram_token(team_with_users)
        client.force_login(owner)

        response = client.post(_unarchive_url(team_with_users, chatbot), {"restore_channels": "on"})

        channel.refresh_from_db()
        assert channel.deleted is True
        assert channel.name in _messages(response)

    def test_restores_one_channel_and_skips_another(self, client, team_with_users, chatbot_with_channel):
        chatbot, claimed = chatbot_with_channel
        free = ExperimentChannel.objects.get_unfiltered_queryset().create(
            team=team_with_users,
            experiment=chatbot,
            name="Free channel",
            platform=ChannelPlatform.WHATSAPP,
            extra_data={"number": "+27820001111"},
            deleted=True,
        )
        _claim_telegram_token(team_with_users)
        client.force_login(team_with_users.members.first())

        client.post(_unarchive_url(team_with_users, chatbot), {"restore_channels": "on"})

        claimed.refresh_from_db()
        free.refresh_from_db()
        assert claimed.deleted is True
        assert free.deleted is False

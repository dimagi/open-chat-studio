"""DELETE /api/v2/chatbots/{id}/ -- soft-archive a chatbot (#4143).

Archiving is "undo my own draft", not a teardown. A chatbot with a messaging channel attached is
refused: detaching one calls out to the upstream provider to remove its webhook, and this API
cannot create a channel, so the agent could not put back what it tore down. The team-wide API, web
and evaluations channels are not attachments and never stand in the way.
"""

import threading

import pytest
from django.db import connections, transaction
from django.utils import timezone

from apps.api.v2.lookups import get_working_chatbot
from apps.api.v2.write.archive import archive_chatbot
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.events.models import EventActionType, ScheduledMessage, TimePeriod
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.events import EventActionFactory, ScheduledMessageFactory
from apps.utils.factories.experiment import ChatbotFactory, ParticipantFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.pytest import django_db_with_data
from apps.utils.tests.clients import ApiTestClient


@pytest.fixture()
def chatbot(db):
    return ChatbotFactory.create(team=TeamWithUsersFactory.create(), name="Support bot", description="")


@pytest.fixture()
def client(chatbot):
    return ApiTestClient(chatbot.team.members.first(), chatbot.team)


def _url(chatbot):
    return f"/api/v2/chatbots/{chatbot.public_id}/"


def _schedule(chatbot, **kwargs) -> ScheduledMessage:
    """A scheduled message on `chatbot`, for a participant of its own.

    Its own participant because `external_id` is derived from (name, experiment, participant) and
    unique across the three, so several messages sharing a participant would collide. A schedule
    per participant is also what the feature actually creates.
    """
    action = EventActionFactory.create(
        action_type=EventActionType.SCHEDULETRIGGER,
        params={
            "name": "Check in",
            "time_period": TimePeriod.DAYS,
            "frequency": 1,
            "repetitions": 1,
            "prompt_text": "Still there?",
            "experiment_id": chatbot.id,
        },
    )
    return ScheduledMessageFactory.create(
        team=chatbot.team,
        experiment=chatbot,
        action=action,
        participant=ParticipantFactory.create(team=chatbot.team),
        **kwargs,
    )


@pytest.mark.django_db()
class TestArchive:
    def test_a_chatbot_with_nothing_attached_archives(self, client, chatbot):
        response = client.delete(_url(chatbot))

        assert response.status_code == 200, response.content
        assert response.json() == {"archived": True, "cancelled_scheduled_messages": 0}
        chatbot.refresh_from_db()
        assert chatbot.is_archived is True

    def test_the_team_wide_channels_do_not_stand_in_the_way(self, client, chatbot):
        """API, web and evaluations channels are one per team and carry no chatbot of their own, so
        a plain web or API bot -- the kind this API creates -- archives freely."""
        ExperimentChannel.objects.get_team_api_channel(chatbot.team)
        ExperimentChannel.objects.get_team_web_channel(chatbot.team)
        ExperimentChannel.objects.get_team_evaluations_channel(chatbot.team)

        assert client.delete(_url(chatbot)).status_code == 200

    def test_a_channel_already_detached_does_not_stand_in_the_way(self, client, chatbot):
        """Detaching soft-deletes, so the row survives its own removal. Counting it would leave a
        chatbot permanently unarchivable."""
        channel = ExperimentChannelFactory.create(team=chatbot.team, experiment=chatbot)
        channel.soft_delete()

        assert client.delete(_url(chatbot)).status_code == 200

    def test_another_chatbots_channel_does_not_stand_in_the_way(self, client, chatbot):
        """The guard is per chatbot, not per team."""
        ExperimentChannelFactory.create(team=chatbot.team, experiment=ChatbotFactory.create(team=chatbot.team))

        assert client.delete(_url(chatbot)).status_code == 200

    def test_a_second_delete_is_a_404(self, client, chatbot):
        """Archived rows are outside every v2 write path's queryset, so a retry after an answer the
        client never saw is safe: the 404 means the first one landed."""
        assert client.delete(_url(chatbot)).status_code == 200

        assert client.delete(_url(chatbot)).status_code == 404


@pytest.mark.django_db()
class TestChannelGuard:
    def test_an_attached_channel_blocks_the_archive_and_is_named(self, client, chatbot):
        channel = ExperimentChannelFactory.create(
            team=chatbot.team, experiment=chatbot, platform=ChannelPlatform.TELEGRAM, name="Support telegram"
        )

        response = client.delete(_url(chatbot))

        assert response.status_code == 409
        body = response.json()
        assert body["channels"] == [{"id": channel.id, "platform": "telegram", "name": "Support telegram"}]
        assert "detach" in body["detail"].lower()

    def test_the_chatbot_is_left_exactly_as_it_was(self, client, chatbot):
        """Nothing partial: the scheduled messages archiving would have destroyed are still there."""
        ExperimentChannelFactory.create(team=chatbot.team, experiment=chatbot)
        _schedule(chatbot)

        assert client.delete(_url(chatbot)).status_code == 409

        chatbot.refresh_from_db()
        assert chatbot.is_archived is False
        assert chatbot.scheduled_messages.count() == 1

    def test_every_attached_channel_is_named(self, client, chatbot):
        """The client has to hand the whole list to a human, so one call reports all of them rather
        than one per retry."""
        channels = [
            ExperimentChannelFactory.create(team=chatbot.team, experiment=chatbot, platform=platform)
            for platform in (ChannelPlatform.TELEGRAM, ChannelPlatform.WHATSAPP)
        ]

        body = client.delete(_url(chatbot)).json()

        assert [entry["platform"] for entry in body["channels"]] == ["telegram", "whatsapp"]
        assert {entry["id"] for entry in body["channels"]} == {channel.id for channel in channels}


@pytest.mark.django_db()
class TestScheduledMessages:
    """Archiving hard-deletes the chatbot's scheduled messages. Blocking on them would deadlock --
    there is no API to drain them -- so the count is reported instead, to keep the destruction
    visible to whoever the client answers to."""

    def test_the_messages_are_deleted_and_counted(self, client, chatbot):
        for _ in range(3):
            _schedule(chatbot)

        response = client.delete(_url(chatbot))

        assert response.json()["cancelled_scheduled_messages"] == 3
        assert chatbot.scheduled_messages.count() == 0

    def test_messages_that_will_never_fire_again_are_counted_too(self, client, chatbot):
        """`archive()` deletes every row, not just the pending ones, so the reported count has to
        cover the finished and already-cancelled ones or it would understate what was destroyed."""
        _schedule(chatbot)
        _schedule(chatbot, is_complete=True)
        _schedule(chatbot, cancelled_at=timezone.now())

        assert client.delete(_url(chatbot)).json()["cancelled_scheduled_messages"] == 3
        assert ScheduledMessage.objects.filter(experiment=chatbot).count() == 0

    def test_another_chatbots_messages_are_left_alone(self, client, chatbot):
        other = ChatbotFactory.create(team=chatbot.team)
        _schedule(other)

        assert client.delete(_url(chatbot)).json()["cancelled_scheduled_messages"] == 0
        assert other.scheduled_messages.count() == 1


@pytest.mark.django_db()
def test_a_read_only_key_cannot_archive(chatbot):
    client = ApiTestClient(chatbot.team.members.first(), chatbot.team, read_only=True)

    assert client.delete(_url(chatbot)).status_code == 403
    chatbot.refresh_from_db()
    assert chatbot.is_archived is False


@pytest.mark.django_db()
def test_a_token_without_the_write_scope_cannot_archive(chatbot):
    client = ApiTestClient(chatbot.team.members.first(), chatbot.team, auth_method="oauth", scopes=["chatbots:read"])

    assert client.delete(_url(chatbot)).status_code == 403


@pytest.mark.django_db()
def test_another_teams_chatbot_is_not_found(chatbot):
    other = TeamWithUsersFactory.create()

    assert ApiTestClient(other.members.first(), other).delete(_url(chatbot)).status_code == 404


@django_db_with_data()
def test_an_archive_racing_another_reports_nothing_rather_than_a_stale_count():
    """Two archives of one chatbot are serialised on its row, so only the winner reports a count.

    The count is read before `archive()` deletes the rows it counts. Unlocked, a second archive
    read that same count while the first was still in flight and answered 200 having deleted none
    of them; it now waits on the row and finds the chatbot archived.
    """
    team = TeamWithUsersFactory.create()
    chatbot = ChatbotFactory.create(team=team, name="Support bot", description="")
    _schedule(chatbot)
    client = ApiTestClient(team.members.first(), team)
    answer = {}

    def archive_over_the_api():
        try:
            response = client.delete(_url(chatbot))
            answer["status_code"] = response.status_code
        finally:
            # The thread's own connection, which the test's transaction machinery does not reach.
            connections.close_all()

    # The first archive, held open mid-flight: the row is locked and the schedules are gone, but
    # nothing is committed, so a reader that took no lock would still count them.
    with transaction.atomic():
        first = get_working_chatbot(team, chatbot.public_id, lock=True)
        assert archive_chatbot(first)["cancelled_scheduled_messages"] == 1

        racer = threading.Thread(target=archive_over_the_api)
        racer.start()
        # Asserted so a racer that finished before the commit fails the test rather than passing it
        # vacuously -- it would have answered from a state this test never meant to exercise.
        racer.join(timeout=1)
        assert racer.is_alive(), "the second archive did not wait for the first"

    racer.join(timeout=10)
    assert not racer.is_alive()
    assert answer["status_code"] == 404
    assert ScheduledMessage.objects.filter(experiment=chatbot).count() == 0

import re
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chatbots.forms import BroadcastMessageForm
from apps.chatbots.tasks import send_broadcast_message
from apps.experiments.models import ExperimentSession, SessionStatus
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory, ParticipantFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def experiment():
    return ExperimentFactory(team=TeamWithUsersFactory())


@pytest.fixture()
def logged_in_client(client, experiment):
    client.force_login(experiment.team.members.first())
    return client


def _broadcast_url(experiment):
    return reverse("chatbots:broadcast_message", args=[experiment.team.slug, experiment.id])


def _dialog(content: str) -> str:
    start = content.index('id="broadcast_message_modal"')
    return content[start : content.index("</dialog>", start)]


@pytest.mark.django_db()
@patch("apps.chatbots.tasks.send_broadcast_message_to_session.delay")
def test_broadcast_reaches_each_participant_once_per_selected_channel(delay, experiment):
    """One send per (participant, channel), against the participant's live session."""
    team = experiment.team
    telegram = ExperimentChannelFactory(team=team, experiment=experiment, platform=ChannelPlatform.TELEGRAM)
    whatsapp = ExperimentChannelFactory(team=team, experiment=experiment, platform=ChannelPlatform.WHATSAPP)
    unselected = ExperimentChannelFactory(team=team, experiment=experiment, platform=ChannelPlatform.SLACK)

    participant = ParticipantFactory(team=team)
    # An older session on the same channel: superseded, so it must not be messaged.
    ExperimentSessionFactory(experiment=experiment, participant=participant, experiment_channel=telegram)
    latest_telegram = ExperimentSessionFactory(
        experiment=experiment, participant=participant, experiment_channel=telegram
    )
    on_whatsapp_too = ExperimentSessionFactory(
        experiment=experiment, participant=participant, experiment_channel=whatsapp
    )
    other_participant = ExperimentSessionFactory(experiment=experiment, experiment_channel=whatsapp)
    ExperimentSessionFactory(experiment=experiment, experiment_channel=unselected)
    # A participant of a different chatbot is not part of this broadcast.
    ExperimentSessionFactory(experiment=ExperimentFactory(team=team), experiment_channel=telegram)

    send_broadcast_message(
        experiment_id=experiment.id,
        channel_ids=[telegram.id, whatsapp.id],
        message="Hi all",
        active_within_days=30,
    )

    messaged = {call.kwargs["session_id"] for call in delay.call_args_list}
    assert delay.call_count == 3, "each (participant, channel) pair must be messaged exactly once"
    assert messaged == {latest_telegram.id, on_whatsapp_too.id, other_participant.id}
    assert {call.kwargs["message"] for call in delay.call_args_list} == {"Hi all"}


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "status",
    [
        pytest.param(SessionStatus.SETUP, id="setup"),
        pytest.param(SessionStatus.PENDING, id="pending"),
        pytest.param(SessionStatus.PENDING_REVIEW, id="pending-review"),
        pytest.param(SessionStatus.COMPLETE, id="complete"),
        pytest.param(SessionStatus.UNKNOWN, id="unknown"),
    ],
)
@patch("apps.chatbots.tasks.send_broadcast_message_to_session.delay")
def test_broadcast_skips_a_session_that_is_not_active(delay, status, experiment):
    """Only a live conversation is broadcast into -- anything else has no participant in it."""
    channel = ExperimentChannelFactory(team=experiment.team, experiment=experiment)
    ExperimentSessionFactory(experiment=experiment, experiment_channel=channel, status=status)

    send_broadcast_message(experiment_id=experiment.id, channel_ids=[channel.id], message="Hi", active_within_days=30)

    delay.assert_not_called()


@pytest.mark.django_db()
@patch("apps.chatbots.tasks.send_broadcast_message_to_session.delay")
def test_broadcast_falls_back_to_an_older_session_that_is_still_active(delay, experiment):
    """Status is filtered before the newest-per-group pick, so an ended newer session is stepped over."""
    channel = ExperimentChannelFactory(team=experiment.team, experiment=experiment)
    participant = ParticipantFactory(team=experiment.team)
    still_active = ExperimentSessionFactory(experiment=experiment, participant=participant, experiment_channel=channel)
    ExperimentSessionFactory(
        experiment=experiment,
        participant=participant,
        experiment_channel=channel,
        status=SessionStatus.PENDING_REVIEW,
    )

    send_broadcast_message(experiment_id=experiment.id, channel_ids=[channel.id], message="Hi", active_within_days=30)

    delay.assert_called_once_with(session_id=still_active.id, message="Hi")


@pytest.mark.django_db()
@patch("apps.chatbots.tasks.send_broadcast_message_to_session.delay")
def test_broadcast_skips_a_session_quiet_for_longer_than_the_activity_window(delay, experiment):
    """The window is what makes a broadcast go to *recently* active participants only."""
    channel = ExperimentChannelFactory(team=experiment.team, experiment=experiment)
    recently_active = ExperimentSessionFactory(
        experiment=experiment, experiment_channel=channel, last_activity_at=timezone.now() - timedelta(days=6)
    )
    ExperimentSessionFactory(
        experiment=experiment, experiment_channel=channel, last_activity_at=timezone.now() - timedelta(days=8)
    )

    send_broadcast_message(experiment_id=experiment.id, channel_ids=[channel.id], message="Hi", active_within_days=7)

    delay.assert_called_once_with(session_id=recently_active.id, message="Hi")


@pytest.mark.django_db()
@patch("apps.chatbots.tasks.send_broadcast_message_to_session.delay")
def test_broadcast_measures_the_window_from_creation_when_no_activity_was_recorded(delay, experiment):
    """`last_activity_at` is only written when the participant sends a message.

    A session that never received one falls back to `created_at`, the same fallback the
    sessions table and its "Last Activity" filter use.
    """
    channel = ExperimentChannelFactory(team=experiment.team, experiment=experiment)
    just_created = ExperimentSessionFactory(experiment=experiment, experiment_channel=channel, last_activity_at=None)
    long_dormant = ExperimentSessionFactory(experiment=experiment, experiment_channel=channel, last_activity_at=None)
    # `created_at` is `auto_now_add`, so it can only be backdated after the fact.
    ExperimentSession.objects.filter(id=long_dormant.id).update(created_at=timezone.now() - timedelta(days=8))

    send_broadcast_message(experiment_id=experiment.id, channel_ids=[channel.id], message="Hi", active_within_days=7)

    delay.assert_called_once_with(session_id=just_created.id, message="Hi")


@pytest.mark.django_db()
@patch("apps.chatbots.tasks.send_broadcast_message_to_session.delay")
def test_broadcast_falls_back_to_an_older_session_inside_the_activity_window(delay, experiment):
    """The window is applied before the newest-per-group pick, as the status filter is."""
    channel = ExperimentChannelFactory(team=experiment.team, experiment=experiment)
    participant = ParticipantFactory(team=experiment.team)
    inside_window = ExperimentSessionFactory(
        experiment=experiment,
        participant=participant,
        experiment_channel=channel,
        last_activity_at=timezone.now() - timedelta(days=2),
    )
    ExperimentSessionFactory(
        experiment=experiment,
        participant=participant,
        experiment_channel=channel,
        last_activity_at=timezone.now() - timedelta(days=8),
    )

    send_broadcast_message(experiment_id=experiment.id, channel_ids=[channel.id], message="Hi", active_within_days=7)

    delay.assert_called_once_with(session_id=inside_window.id, message="Hi")


@pytest.mark.django_db()
def test_chatbot_home_offers_every_enabled_channel(experiment, logged_in_client):
    """Anything a scheduled message can go out on can carry a broadcast, the chat widget included."""
    telegram = ExperimentChannelFactory(team=experiment.team, experiment=experiment, platform=ChannelPlatform.TELEGRAM)
    widget = ExperimentChannelFactory(
        team=experiment.team, experiment=experiment, platform=ChannelPlatform.EMBEDDED_WIDGET
    )
    ExperimentChannelFactory(
        team=experiment.team, experiment=experiment, platform=ChannelPlatform.WHATSAPP, enabled=False
    )
    # The API, web and evaluations channels hang off the team, not the chatbot, so they can't
    # appear here -- there is no per-chatbot channel for a broadcast to select.
    ExperimentChannel.objects.get_team_api_channel(experiment.team)
    ExperimentChannel.objects.get_team_web_channel(experiment.team)
    ExperimentChannel.objects.get_team_evaluations_channel(experiment.team)

    response = logged_in_client.get(reverse("chatbots:single_chatbot_home", args=[experiment.team.slug, experiment.id]))

    assert response.status_code == 200
    assert set(response.context["broadcast_form"].eligible_channels) == {telegram, widget}
    content = response.content.decode()
    assert "Broadcast message" in content
    assert _broadcast_url(experiment) in content


@pytest.mark.django_db()
def test_broadcast_channel_is_labelled_by_platform_alone(experiment, logged_in_client):
    """The channel name defaults to the chatbot's, so it says nothing the reader doesn't know."""
    ExperimentChannelFactory(
        team=experiment.team, experiment=experiment, platform=ChannelPlatform.WHATSAPP, name=experiment.name
    )

    content = logged_in_client.get(
        reverse("chatbots:single_chatbot_home", args=[experiment.team.slug, experiment.id])
    ).content.decode()

    dialog = _dialog(content)
    assert "WhatsApp" in dialog
    assert experiment.name not in dialog


@pytest.mark.django_db()
def test_broadcast_dialog_offers_the_activity_window(experiment, logged_in_client):
    """The cutoff is the sender's to choose, pre-filled and floored at a single day."""
    ExperimentChannelFactory(team=experiment.team, experiment=experiment)

    dialog = _dialog(
        logged_in_client.get(
            reverse("chatbots:single_chatbot_home", args=[experiment.team.slug, experiment.id])
        ).content.decode()
    )

    window = re.search(r'<input[^>]*name="active_within_days"[^>]*>', dialog)
    assert window, dialog
    assert 'min="1"' in window.group()
    assert f'value="{BroadcastMessageForm.DEFAULT_ACTIVE_WITHIN_DAYS}"' in window.group()
    assert f'max="{BroadcastMessageForm.MAX_ACTIVE_WITHIN_DAYS}"' in window.group()


@pytest.mark.django_db()
def test_broadcast_modal_arms_the_whatsapp_template_warning(experiment, logged_in_client):
    """Each checkbox carries its platform, which is what the warning keys off."""
    telegram = ExperimentChannelFactory(team=experiment.team, experiment=experiment, platform=ChannelPlatform.TELEGRAM)
    whatsapp = ExperimentChannelFactory(team=experiment.team, experiment=experiment, platform=ChannelPlatform.WHATSAPP)

    content = logged_in_client.get(
        reverse("chatbots:single_chatbot_home", args=[experiment.team.slug, experiment.id])
    ).content.decode()

    assert re.search(rf'value="{whatsapp.id}"[^>]*data-platform="whatsapp"', content)
    assert re.search(rf'value="{telegram.id}"[^>]*data-platform="telegram"', content)
    assert "new_bot_message" in content
    assert "whatsapp_meta_cloud_api/#create-the-required-template-in-meta-business-manager" in content


@pytest.mark.django_db()
def test_chatbot_home_hides_the_broadcast_button_without_a_channel(experiment, logged_in_client):
    response = logged_in_client.get(reverse("chatbots:single_chatbot_home", args=[experiment.team.slug, experiment.id]))

    assert response.status_code == 200
    assert "Broadcast message" not in response.content.decode()


@pytest.mark.django_db()
@patch("apps.chatbots.views.send_broadcast_message.delay")
def test_broadcast_view_queues_the_selected_channels(delay, experiment, logged_in_client):
    telegram = ExperimentChannelFactory(team=experiment.team, experiment=experiment, platform=ChannelPlatform.TELEGRAM)

    response = logged_in_client.post(
        _broadcast_url(experiment),
        data={"channels": [telegram.id], "message": "We're back online", "active_within_days": 14},
    )

    assert response.status_code == 302
    delay.assert_called_once_with(
        experiment_id=experiment.id,
        channel_ids=[telegram.id],
        message="We're back online",
        active_within_days=14,
    )


@pytest.mark.django_db()
@patch("apps.chatbots.views.send_broadcast_message.delay")
def test_broadcast_view_rejects_a_chatbot_that_is_not_editable(delay, experiment, logged_in_client):
    """An archived chatbot can't be broadcast on, even by POSTing the endpoint directly."""
    telegram = ExperimentChannelFactory(team=experiment.team, experiment=experiment, platform=ChannelPlatform.TELEGRAM)
    experiment.archive()

    response = logged_in_client.post(
        _broadcast_url(experiment),
        data={"channels": [telegram.id], "message": "Hi", "active_within_days": 30},
    )

    assert response.status_code == 404
    delay.assert_not_called()


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("channel", "message", "days"),
    [
        pytest.param(None, "Hi", "30", id="no-channel-selected"),
        pytest.param("other-chatbot", "Hi", "30", id="channel-of-another-chatbot"),
        pytest.param("disabled", "Hi", "30", id="disabled-channel"),
        pytest.param("own", "", "30", id="empty-message"),
        pytest.param("own", "x" * (BroadcastMessageForm.MESSAGE_CHAR_LIMIT + 1), "30", id="message-over-char-limit"),
        pytest.param("own", "Hi", "", id="no-activity-window"),
        pytest.param("own", "Hi", "0", id="activity-window-under-a-day"),
        pytest.param("own", "Hi", "-7", id="negative-activity-window"),
        pytest.param(
            "own",
            "Hi",
            str(BroadcastMessageForm.MAX_ACTIVE_WITHIN_DAYS + 1),
            id="activity-window-over-the-cap",
        ),
        pytest.param("own", "Hi", "seven", id="non-numeric-activity-window"),
    ],
)
@patch("apps.chatbots.views.send_broadcast_message.delay")
def test_broadcast_view_rejects_invalid_input(delay, channel, message, days, experiment, logged_in_client):
    """Nothing is queued unless a live channel of *this* chatbot, a message within the limit and a
    window of at least one day are given."""
    channels = {
        "own": ExperimentChannelFactory(team=experiment.team, experiment=experiment),
        "other-chatbot": ExperimentChannelFactory(team=experiment.team),
        "disabled": ExperimentChannelFactory(
            team=experiment.team, experiment=experiment, platform=ChannelPlatform.WHATSAPP, enabled=False
        ),
    }
    data = {"message": message, "active_within_days": days}
    if channel:
        data["channels"] = [channels[channel].id]

    response = logged_in_client.post(_broadcast_url(experiment), data=data)

    assert response.status_code == 302
    delay.assert_not_called()

import pytest
from django.test import override_settings
from django.urls import reverse

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.experiments.exceptions import ChannelAlreadyUtilizedException
from apps.service_providers.models import MessagingProviderType
from apps.teams.models import Flag
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.service_provider_factories import MessagingProviderFactory


@pytest.mark.django_db()
def test_new_integration_does_not_raise_exception():
    channel = ExperimentChannelFactory()
    new_experiment = ExperimentFactory()

    ExperimentChannel.check_usage_by_another_experiment(
        channel.platform, identifier="321", new_experiment=new_experiment
    )


@pytest.mark.django_db()
def test_duplicate_integration_raises_exception():
    channel = ExperimentChannelFactory()
    new_experiment = ExperimentFactory()

    with pytest.raises(ChannelAlreadyUtilizedException):
        ExperimentChannel.check_usage_by_another_experiment(
            channel.platform,
            identifier=channel.extra_data["bot_token"],
            new_experiment=new_experiment,
        )


@pytest.mark.django_db()
def test_channel_webhook_url():
    # Setup providers
    twilio_provider = MessagingProviderFactory(type=MessagingProviderType.twilio)
    turnio_provider = MessagingProviderFactory(type=MessagingProviderType.turnio)

    # Setup channels with their respective providers
    no_provider_channel = ExperimentChannelFactory()
    twilio_channel = ExperimentChannelFactory(messaging_provider=twilio_provider)
    turnio_channel = ExperimentChannelFactory(messaging_provider=turnio_provider)

    # Let's check out each one's webhook url
    assert no_provider_channel.webhook_url is None
    assert reverse("channels:new_twilio_message") in twilio_channel.webhook_url
    turnio_uri = reverse("channels:new_turn_message", kwargs={"experiment_id": turnio_channel.experiment.public_id})
    assert turnio_uri in turnio_channel.webhook_url


@pytest.mark.django_db()
def test_deleting_experiment_channel_only_removes_the_experiment_channel():
    """Test to make sure that removing an experiment channel does not remove important related records"""
    experiment = ExperimentFactory(conversational_consent_enabled=True)
    experiment_channel = ExperimentChannelFactory(experiment=experiment)
    chat = Chat.objects.create(team=experiment.team)
    chat_messsage = ChatMessage.objects.create(chat=chat, content="Hi", message_type=ChatMessageType.HUMAN)
    experiment_session = ExperimentSessionFactory(
        experiment=experiment, experiment_channel=experiment_channel, participant__user=experiment.owner
    )
    experiment_session.chat = chat
    experiment_session.save()

    def _assert_not_deleted(instance):
        instance.refresh_from_db()
        assert instance is not None
        assert instance.id is not None

    # Let's check soft delete first
    experiment_channel.soft_delete()
    experiment_channel.refresh_from_db()
    assert experiment_channel.deleted is True

    # Let's check actual delete
    experiment_channel.delete()
    _assert_not_deleted(chat)
    _assert_not_deleted(chat_messsage)
    _assert_not_deleted(experiment)
    _assert_not_deleted(experiment_session)
    _assert_not_deleted(chat)


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("slack_enabled", "messaging_provider_types", "channels_enabled"),
    [
        (False, [], [ChannelPlatform.TELEGRAM]),
        (
            False,
            [MessagingProviderType.twilio],
            [ChannelPlatform.TELEGRAM, ChannelPlatform.WHATSAPP, ChannelPlatform.FACEBOOK],
        ),
        (False, [MessagingProviderType.turnio], [ChannelPlatform.TELEGRAM, ChannelPlatform.WHATSAPP]),
        (
            False,
            [MessagingProviderType.turnio, MessagingProviderType.twilio],
            [ChannelPlatform.TELEGRAM, ChannelPlatform.WHATSAPP, ChannelPlatform.FACEBOOK],
        ),
        (False, [MessagingProviderType.sureadhere], [ChannelPlatform.TELEGRAM, ChannelPlatform.SUREADHERE]),
        (
            True,
            [MessagingProviderType.sureadhere, MessagingProviderType.slack],
            [ChannelPlatform.TELEGRAM, ChannelPlatform.SLACK, ChannelPlatform.SUREADHERE],
        ),
    ],
)
def test_available_channels(slack_enabled, messaging_provider_types, channels_enabled, experiment):
    for provider_type in messaging_provider_types:
        _build_provider(provider_type, team=experiment.team)

    all_platforms = ChannelPlatform.as_list(
        exclude=[ChannelPlatform.API, ChannelPlatform.WEB, ChannelPlatform.EVALUATIONS]
    )
    expected_status = {platform: False for platform in all_platforms}
    for platform in channels_enabled:
        expected_status[platform] = True

    with override_settings(SLACK_ENABLED=slack_enabled):
        for platform, enabled in ChannelPlatform.for_dropdown(used_platforms=set(), team=experiment.team).items():
            assert expected_status[platform] == enabled


def _build_provider(provider_type: MessagingProviderType, team):
    config = {}
    match provider_type:
        case MessagingProviderType.twilio:
            config = {"auth_token": "test_key", "account_sid": "test_secret"}
        case MessagingProviderType.turnio:
            config = {"auth_token": "test_key"}
        case MessagingProviderType.sureadhere:
            config = {"client_id": "", "client_secret": "", "client_scope": "", "base_url": "", "auth_url": ""}
        case MessagingProviderType.slack:
            config = {"slack_team_id": "", "slack_installation_id": 123}
    MessagingProviderFactory(type=provider_type, team=team, config=config)


@override_settings(WAFFLE_CREATE_MISSING_FLAGS=True)
def test_is_active_for_team_creates_missing_flag(experiment):
    flag = Flag.get("flag_missing_flag_1")
    is_active = flag.is_active_for_team(experiment.team)
    assert is_active is False
    assert flag.id is not None


@override_settings(WAFFLE_CREATE_MISSING_FLAGS=False)
def test_is_active_for_team_does_not_create_missing_flag(experiment):
    flag = Flag.get("flag_missing_flag_2")
    is_active = flag.is_active_for_team(experiment.team)
    assert is_active is False
    assert flag.id is None


@pytest.mark.django_db()
def test_get_team_evaluations_channel(team_with_users):
    """Test that get_team_evaluations_channel creates and returns a team evaluations channel"""
    team = team_with_users

    # Should create a new evaluations channel
    channel = ExperimentChannel.objects.get_team_evaluations_channel(team)
    assert channel.platform == ChannelPlatform.EVALUATIONS
    assert channel.team == team
    assert channel.name == f"{team.slug}-evaluations-channel"

    # Should return the same channel on subsequent calls
    channel2 = ExperimentChannel.objects.get_team_evaluations_channel(team)
    assert channel.id == channel2.id


class TestChannelPlatform:
    def test_normalize_identifier(self):
        identifier = "abc"
        for platform in ChannelPlatform:
            normalized_id = platform.normalize_identifier(identifier)
            assert normalized_id == "abc"

        identifier = "ABC"
        for platform in ChannelPlatform:
            normalized_id = platform.normalize_identifier(identifier)
            if platform == ChannelPlatform.COMMCARE_CONNECT:
                assert normalized_id == "abc"
            else:
                assert normalized_id == "ABC"

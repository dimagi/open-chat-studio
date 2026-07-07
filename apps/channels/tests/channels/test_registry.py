import pytest
from django.test import override_settings

from apps.channels.channels_v2.channel_base import ChannelBase
from apps.channels.channels_v2.registry import from_experiment_session
from apps.channels.models import ChannelPlatform
from apps.experiments.models import ParticipantData
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory


@pytest.mark.django_db()
@override_settings(COMMCARE_CONNECT_SERVER_SECRET="123", COMMCARE_CONNECT_SERVER_ID="123")
@pytest.mark.parametrize("platform", [platform for platform, _ in ChannelPlatform.choices])
def test_all_channels_can_be_instantiated_from_a_session(platform, twilio_provider):
    """This test checks all channel types and makes sure that we can instantiate each one by calling
    `from_experiment_session`. For the sake of ease, we assume all platforms uses the Twilio
    messenging provider.
    """
    if platform == ChannelPlatform.EVALUATIONS:
        pytest.skip("Evaluations channel can't be instantiated from a session")
    session = ExperimentSessionFactory.create(experiment_channel__platform=platform)
    ParticipantData.objects.create(
        team=session.team,
        experiment=session.experiment,
        data={},
        participant=session.participant,
        system_metadata={"consent": True},
    )
    channel = from_experiment_session(session)
    assert type(channel) in ChannelBase.__subclasses__()


@pytest.mark.django_db()
def test_missing_channel_raises_error(twilio_provider):
    experiment = ExperimentFactory.create()
    experiment_channel = ExperimentChannelFactory.create(
        messaging_provider=twilio_provider, experiment=experiment, platform="whatsapp"
    )
    session = ExperimentSessionFactory.create(experiment_channel=experiment_channel)
    session.experiment_channel.platform = "snail_mail"
    with pytest.raises(Exception, match="Unsupported platform type snail_mail"):
        from_experiment_session(session)

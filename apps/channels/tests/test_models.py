import pytest

from apps.channels.models import ExperimentChannel
from apps.experiments.exceptions import ChannelAlreadyUtilizedException
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory


def test_new_integration_does_not_raise_exception(db):
    channel = ExperimentChannelFactory()
    new_experiment = ExperimentFactory()

    ExperimentChannel.check_usage_by_another_experiment(
        channel.platform, identifier="321", new_experiment=new_experiment
    )


def test_duplicate_integration_raises_exception(db):
    channel = ExperimentChannelFactory()
    new_experiment = ExperimentFactory()

    with pytest.raises(ChannelAlreadyUtilizedException):
        ExperimentChannel.check_usage_by_another_experiment(
            channel.platform, identifier=channel.extra_data["bot_token"], new_experiment=new_experiment
        )

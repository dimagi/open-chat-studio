import factory

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.utils.factories import experiment as experiment_factory
from apps.utils.factories import team as team_factory


class ExperimentChannelFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ExperimentChannel

    team = factory.SubFactory(team_factory.TeamFactory)
    name = factory.Faker("name")
    experiment = factory.SubFactory(experiment_factory.ExperimentFactory)
    platform = ChannelPlatform.TELEGRAM
    extra_data = {"bot_token": "123"}

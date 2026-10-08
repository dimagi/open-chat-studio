import factory
import factory.django

from apps.moderation.models import DeniedParticipant, DeniedParticipantSource
from apps.utils.factories.experiment import ParticipantFactory
from apps.utils.factories.team import TeamFactory


class DeniedParticipantFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = DeniedParticipant

    team = factory.SubFactory(TeamFactory)
    participant = factory.SubFactory(ParticipantFactory, team=factory.SelfAttribute("..team"))
    source = DeniedParticipantSource.MANUAL

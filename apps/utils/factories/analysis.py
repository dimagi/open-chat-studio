import factory
import factory.django

from apps.analysis.models import AnalysisQuery, TranscriptAnalysis
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory


class TranscriptAnalysisFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TranscriptAnalysis

    team = factory.SubFactory(TeamFactory)
    experiment = factory.SubFactory(ExperimentFactory, team=factory.SelfAttribute("..team"))
    created_by = factory.SubFactory(UserFactory)
    name = factory.Sequence(lambda n: f"Analysis {n}")


class AnalysisQueryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AnalysisQuery

    analysis = factory.SubFactory(TranscriptAnalysisFactory)
    name = factory.Sequence(lambda n: f"Query {n}")
    prompt = "Summarise the conversation"
    output_format = "text"

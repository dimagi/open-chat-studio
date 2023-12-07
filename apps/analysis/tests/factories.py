import factory

from apps.analysis.models import Analysis, RunGroup
from apps.analysis.pipelines import LLM_PIPE, TEXT_DATA_PIPE
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.factories.team import TeamFactory


class AnalysisFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Analysis

    name = factory.Faker("name")
    team = factory.SubFactory(TeamFactory)
    llm_provider = factory.SubFactory(LlmProviderFactory, team=factory.SelfAttribute("..team"))
    source = TEXT_DATA_PIPE
    pipeline = "test"


class RunGroupFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = RunGroup

    analysis = factory.SubFactory(AnalysisFactory)
    team = factory.SelfAttribute("..analysis.team")

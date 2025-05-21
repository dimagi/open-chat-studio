import factory
from factory.django import DjangoModelFactory

from apps.evaluations.models import (
    EvaluationConfig,
    EvaluationDataset,
    EvaluationMessage,
    EvaluationRun,
    Evaluator,
)
from apps.utils.factories.experiment import ChatMessageFactory
from apps.utils.factories.team import TeamFactory


class EvaluatorFactory(DjangoModelFactory):
    class Meta:
        model = Evaluator

    team = factory.SubFactory(TeamFactory)
    type = "LLM"
    params = {
        "llm_prompt": "give me the sentiment of the user messages",
        "output_schema": {"sentiment": "the sentiment"},
    }


class EvaluationMessageFactory(DjangoModelFactory):
    class Meta:
        model = EvaluationMessage

    team = factory.SubFactory(TeamFactory)


class EvaluationDatasetFactory(DjangoModelFactory):
    class Meta:
        model = EvaluationDataset

    team = factory.SubFactory(TeamFactory)
    message_type = "ALL"

    @factory.post_generation
    def messages(self, create, extracted, **kwargs):
        if not create:
            return
        if extracted:
            for message in extracted:
                self.messages.add(message)
        else:
            self.messages.add(ChatMessageFactory())


class EvaluationConfigFactory(DjangoModelFactory):
    class Meta:
        model = EvaluationConfig

    team = factory.SubFactory(TeamFactory)
    dataset = factory.SubFactory(EvaluationDatasetFactory)

    @factory.post_generation
    def evaluators(self, create, extracted, **kwargs):
        if not create:
            return
        if extracted:
            for evaluator in extracted:
                self.evaluators.add(evaluator)
        else:
            self.evaluators.add(EvaluatorFactory())


class EvaluationRunFactory(DjangoModelFactory):
    class Meta:
        model = EvaluationRun

    team = factory.SubFactory(TeamFactory)
    config = factory.SubFactory(EvaluationConfigFactory)

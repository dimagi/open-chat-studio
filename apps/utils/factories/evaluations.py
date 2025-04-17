import factory
from factory.django import DjangoModelFactory

from apps.evaluations.models import (
    EvaluationConfig,
    EvaluationDataset,
    EvaluationResult,
    EvaluationRun,
    Evaluator,
)
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory


class EvaluatorFactory(DjangoModelFactory):
    class Meta:
        model = Evaluator

    type = "LLM"
    params = {
        "llm_prompt": "give me the sentiment of the user messages",
        "output_schema": {"sentiment": "the sentiment"},
    }


class EvaluationDatasetFactory(DjangoModelFactory):
    class Meta:
        model = EvaluationDataset

    message_type = "ALL"
    version = factory.SubFactory(ExperimentFactory)

    @factory.post_generation
    def sessions(self, create, extracted, **kwargs):
        if not create:
            return
        if extracted:
            for session in extracted:
                self.sessions.add(session)
        else:
            self.sessions.add(ExperimentSessionFactory())


class EvaluationConfigFactory(DjangoModelFactory):
    class Meta:
        model = EvaluationConfig

    dataset = factory.SubFactory(EvaluationDatasetFactory)
    experiment = factory.SubFactory(ExperimentFactory)

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

    config = factory.SubFactory(EvaluationConfigFactory)


class EvaluatorResultFactory(DjangoModelFactory):
    class Meta:
        model = EvaluationResult

    evaluator = factory.SubFactory(EvaluatorFactory)
    output = factory.LazyFunction(lambda: {"sentiment": "positive"})
    run = factory.SubFactory(EvaluationRunFactory)

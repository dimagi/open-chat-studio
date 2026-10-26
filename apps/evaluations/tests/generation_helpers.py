"""Builders shared by the tests that run a chatbot generation through an evaluation run."""

from unittest.mock import patch

from apps.evaluations.models import EvaluationRun
from apps.evaluations.tasks import evaluate_message, run_bot_generation
from apps.pipelines.tests.utils import create_pipeline_model
from apps.utils.factories.evaluations import EvaluationConfigFactory, EvaluationRunFactory, EvaluatorFactory
from apps.utils.factories.experiment import ChatbotFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.tests.langchain import build_fake_llm_service


def python_evaluator(team, code, **params):
    return EvaluatorFactory.create(
        team=team,
        type="PythonEvaluator",
        params={"code": code, **params},
        llm_provider=None,
        llm_provider_model=None,
    )


def llm_experiment(team, build_nodes):
    """A chatbot whose pipeline is `build_nodes(provider_id, model_id)`."""
    provider = LlmProviderFactory.create(team=team)
    model = LlmProviderModelFactory.create(team=team, type=provider.type)
    experiment = ChatbotFactory.create(team=team)
    create_pipeline_model(build_nodes(str(provider.id), str(model.id)), pipeline=experiment.pipeline)
    experiment.pipeline.save()
    return experiment


def generate(team, message, experiment, responses=()):
    with patch(
        "apps.service_providers.models.LlmProvider.get_llm_service",
        return_value=build_fake_llm_service(responses=list(responses)),
    ):
        return run_bot_generation(team, message, experiment, evaluation_run=EvaluationRunFactory.create(team=team))


def run_with(team, message, experiment, *evaluators, responses=()):
    """Evaluate `message` against `experiment`'s generation with `evaluators`, in the order given.

    The LLM service is patched only when `responses` is given.
    """
    config = EvaluationConfigFactory.create(
        team=team, dataset=message.evaluationdataset_set.first(), evaluators=list(evaluators)
    )
    run = EvaluationRun.objects.create(team=team, config=config, generation_experiment=experiment)
    evaluator_ids = [evaluator.id for evaluator in evaluators]
    if not responses:
        evaluate_message(run.id, evaluator_ids, message.id)
        return run
    with patch(
        "apps.service_providers.models.LlmProvider.get_llm_service",
        return_value=build_fake_llm_service(responses=list(responses)),
    ):
        evaluate_message(run.id, evaluator_ids, message.id)
    return run

"""A node pointing at a deprecated LLM model keeps working until the model is deleted.

Deprecation is the warning stage of the model lifecycle (`docs/developer_guides/managing_models.md`):
it gives a team a migration window, announced by `notify_deprecated_models`. Deletion --
`remove_deprecated_models` -- is the stage that repoints or clears the references. So nothing here
may block, and the editor says so out of band instead.
"""

from unittest import mock

import pytest

from apps.chatbots.views import CreateChatbotVersion
from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.repository import ORMRepository
from apps.pipelines.tasks import get_response_for_pipeline_test_message
from apps.pipelines.tests.utils import create_pipeline_model, create_runnable, end_node, llm_response_node, start_node
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.factories.user import UserFactory
from apps.utils.tests.langchain import build_fake_llm_service


@pytest.fixture()
def provider():
    return LlmProviderFactory.create()


@pytest.fixture()
def deprecated_model(provider):
    return LlmProviderModelFactory.create(team=provider.team, type=provider.type, deprecated=True)


@pytest.fixture()
def pipeline(provider):
    return PipelineFactory.create(team=provider.team)


def _nodes(provider, model):
    return [start_node(), llm_response_node(str(provider.id), str(model.id)), end_node()]


@pytest.mark.django_db()
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_deprecated_model_still_runs(get_llm_service, provider, deprecated_model, pipeline):
    """The live-bot case: a deploy that deprecates a model must not take running chatbots down."""
    get_llm_service.return_value = build_fake_llm_service(responses=["123"])

    state = create_runnable(pipeline, _nodes(provider, deprecated_model)).invoke(
        PipelineState(messages=["Repeat exactly: 123"]), config={"configurable": {"repo": ORMRepository()}}
    )

    assert state["messages"][-1] == "123"


@pytest.mark.django_db()
def test_deprecated_model_is_not_a_validation_error(provider, deprecated_model, pipeline):
    """`validate()` feeds the editor, the test-message run and version creation alike, so a
    deprecated model reported here blocks all three."""
    create_pipeline_model(_nodes(provider, deprecated_model), pipeline=pipeline)

    assert pipeline.validate(full=False) == {"node": {}, "edge": [], "pipeline": []}


@pytest.mark.django_db()
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_deprecated_model_does_not_block_the_test_message(get_llm_service, provider, deprecated_model, pipeline):
    get_llm_service.return_value = build_fake_llm_service(responses=["123"])
    create_pipeline_model(_nodes(provider, deprecated_model), pipeline=pipeline).save()
    user = UserFactory.create()
    ExperimentFactory.create(team=provider.team, pipeline=pipeline, owner=user)

    response = get_response_for_pipeline_test_message(pipeline.id, "hi", user.id)

    assert "error" not in response


@pytest.mark.django_db()
def test_deprecated_model_does_not_block_version_creation(provider, deprecated_model, pipeline):
    """`CreateChatbotVersion` refuses to version a pipeline with errors, so a deprecation used to
    freeze a chatbot's releases as well as its runs."""
    create_pipeline_model(_nodes(provider, deprecated_model), pipeline=pipeline).save()
    view = CreateChatbotVersion()
    view.object = ExperimentFactory.create(team=provider.team, pipeline=pipeline)

    assert view._check_pipleline_for_errors() is None

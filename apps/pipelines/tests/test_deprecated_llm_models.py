"""A node pointing at a deprecated LLM model keeps working until the model is deleted.

Deprecation is the warning stage of the model lifecycle (`docs/developer_guides/managing_models.md`):
it gives a team a migration window, announced by `notify_deprecated_models`. Deletion --
`remove_deprecated_models` -- is the stage that repoints or clears the references. So nothing here
may block, and the editor says so out of band instead.
"""

import json
from unittest import mock

import pytest
from django.test import Client
from django.urls import reverse

from apps.chatbots.views import CreateChatbotVersion
from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.repository import ORMRepository
from apps.pipelines.tasks import get_response_for_pipeline_test_message
from apps.pipelines.tests.utils import create_pipeline_model, create_runnable, end_node, llm_response_node, start_node
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.factories.team import TeamWithUsersFactory
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


@pytest.mark.django_db()
class TestEditorEndpoint:
    """The editor's own read and save. Neither computes anything of its own -- the point is that
    both responses carry the map, since the badge would silently vanish on whichever one did not."""

    @pytest.fixture()
    def team(self):
        return TeamWithUsersFactory.create()

    @pytest.fixture()
    def provider(self, team):
        return LlmProviderFactory.create(team=team)

    @pytest.fixture()
    def authed_client(self, team):
        client = Client()
        client.force_login(team.members.first())
        return client

    def _url(self, pipeline):
        return reverse("pipelines:pipeline_data", kwargs={"team_slug": pipeline.team.slug, "pk": pipeline.id})

    def test_the_read_reports_the_deprecated_model(self, authed_client, provider, deprecated_model, pipeline):
        nodes = _nodes(provider, deprecated_model)
        create_pipeline_model(nodes, pipeline=pipeline).save()

        response = authed_client.get(self._url(pipeline))

        assert response.status_code == 200
        body = response.json()["pipeline"]
        assert body["errors"] == {"node": {}, "edge": [], "pipeline": []}
        assert body["deprecated_models"] == {nodes[1]["id"]: {"model": deprecated_model.name, "replacement": None}}

    def test_a_save_reports_the_deprecated_model(self, authed_client, provider, deprecated_model, pipeline):
        nodes = _nodes(provider, deprecated_model)
        create_pipeline_model(nodes, pipeline=pipeline).save()

        response = authed_client.patch(
            self._url(pipeline),
            data=json.dumps({"base_revision": pipeline.edit_revision, "name": "Renamed"}),
            content_type="application/json",
        )

        assert response.status_code == 200, response.content
        assert response.json()["deprecated_models"] == {
            nodes[1]["id"]: {"model": deprecated_model.name, "replacement": None}
        }

    def test_a_full_graph_save_reports_the_deprecated_model(self, authed_client, provider, deprecated_model, pipeline):
        """The POST path, which the editor still uses for a whole-graph save. It builds its own
        response dict rather than sharing the PATCH one, so it can carry the key or not on its own."""
        nodes = _nodes(provider, deprecated_model)
        create_pipeline_model(nodes, pipeline=pipeline).save()

        response = authed_client.post(
            self._url(pipeline),
            data=json.dumps({"name": pipeline.name, "data": pipeline.flow_data}),
            content_type="application/json",
        )

        assert response.status_code == 200, response.content
        assert response.json()["deprecated_models"] == {
            nodes[1]["id"]: {"model": deprecated_model.name, "replacement": None}
        }

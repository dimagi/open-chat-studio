from unittest import mock
from unittest.mock import Mock, patch

import pytest

from apps.channels.models import ExperimentChannel
from apps.experiments.models import Experiment, ExperimentSession, Participant
from apps.pipelines.tests.utils import create_runnable, end_node, llm_response_with_prompt_node, start_node
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory
from apps.utils.factories.service_provider_factories import (
    LlmProviderFactory,
    LlmProviderModelFactory,
)
from apps.utils.factories.user import UserFactory
from apps.utils.langchain import (
    FakeLlmEcho,
    build_fake_llm_service,
)
from apps.utils.pytest import django_db_with_data


@pytest.mark.django_db()
def test_archive_pipeline_archives_nodes_as_well():
    pipeline = PipelineFactory()
    assert pipeline.node_set.count() > 0
    pipeline.archive()
    assert pipeline.node_set.count() == 0


@pytest.mark.django_db()
class TestNode:
    @pytest.mark.parametrize("versioned_assistant_linked", [True, False])
    @patch("apps.assistants.sync.push_assistant_to_openai", Mock())
    def test_versioning_assistant_node(self, versioned_assistant_linked):
        """
        Versioning an assistant node should version the assistant as well, but only when the linked assistant is not
        already a version
        """
        assistant = OpenAiAssistantFactory()
        if versioned_assistant_linked:
            assistant = assistant.create_new_version()

        pipeline = PipelineFactory()
        NodeFactory(type="AssistantNode", pipeline=pipeline, params={"assistant_id": assistant.id})
        assert pipeline.node_set.filter(type="AssistantNode").exists()

        pipeline.create_new_version()

        original_node = pipeline.node_set.get(type="AssistantNode")
        node_version = pipeline.versions.first().node_set.get(type="AssistantNode")
        assistant_version = assistant if versioned_assistant_linked else assistant.versions.first()

        original_node_assistant_id = original_node.params["assistant_id"]
        node_version_assistant_id = node_version.params["assistant_id"]

        if versioned_assistant_linked:
            assert original_node_assistant_id == node_version_assistant_id == assistant.id
        else:
            assert original_node_assistant_id != node_version_assistant_id
            assert original_node_assistant_id == assistant.id
            assert node_version_assistant_id == assistant_version.id


class TestPipeline:
    @pytest.mark.django_db()
    @pytest.mark.parametrize("participant_exists", [True, False])
    def test_simple_invoke(self, participant_exists, team_with_users):
        """Test that the mock data is not being persisted when doing a simple invoke"""
        team = team_with_users
        requesting_user = team_with_users.members.first()
        pipeline = PipelineFactory(team=team)
        temporary_instance_models = [
            ExperimentSession,
            Experiment,
            ExperimentChannel,
        ]

        if participant_exists:
            Participant.objects.create(user=requesting_user, team=team)
        else:
            temporary_instance_models.append(Participant)

        for model in temporary_instance_models:
            assert model.objects.count() == 0

        pipeline.simple_invoke("test", requesting_user.id)

        for model in temporary_instance_models:
            assert model.objects.count() == 0

        if participant_exists:
            assert Participant.objects.filter(user=requesting_user).exists()

    @django_db_with_data(available_apps=("apps.service_providers", "apps.users"))
    @mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
    @mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
    def test_simple_invoke_with_pipeline(self, get_llm_service):
        """Test simple invoke with a pipeline that has an LLM node"""
        provider = LlmProviderFactory()
        provider_model = LlmProviderModelFactory()
        llm = FakeLlmEcho()
        service = build_fake_llm_service(None, [0], llm)
        get_llm_service.return_value = service

        llm_node = llm_response_with_prompt_node(
            str(provider.id),
            str(provider_model.id),
            prompt="Help the user. User data: {participant_data}. Source material: {source_material}",
            history_type="global",
        )
        nodes = [start_node(), llm_node, end_node()]
        pipeline = PipelineFactory()
        create_runnable(pipeline, nodes)
        pipeline.save()

        user_input = "The User Input"
        user = UserFactory()
        pipeline.simple_invoke(user_input, user.id)["messages"][-1]
        expected_call_messages = [
            [("system", "Help the user. User data: . Source material: "), ("human", user_input)],
        ]
        assert [
            [(message.type, message.content) for message in call] for call in llm.get_call_messages()
        ] == expected_call_messages

        assert ExperimentSession.objects.count() == 0

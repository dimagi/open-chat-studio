from unittest import mock
from unittest.mock import Mock, patch

import pytest

from apps.assistants.models import OpenAiAssistant
from apps.channels.models import ExperimentChannel
from apps.documents.models import Collection
from apps.events.models import EventActionType
from apps.experiments.models import Experiment, ExperimentSession, Participant
from apps.pipelines.nodes.nodes import AssistantNode, LLMResponseWithPrompt
from apps.pipelines.tests.utils import create_runnable, end_node, llm_response_with_prompt_node, start_node
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.events import EventActionFactory, ExperimentFactory, StaticTriggerFactory
from apps.utils.factories.experiment import SourceMaterialFactory
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
class TestVersioningNodes:
    @pytest.mark.parametrize("versioned_assistant_linked", [True, False])
    @patch("apps.assistants.sync.push_assistant_to_openai", Mock())
    def test_version_assistant_node(self, versioned_assistant_linked):
        """
        Versioning an assistant node should version the assistant as well, but only when the linked assistant is not
        already a version
        """
        node_type = AssistantNode.__name__
        assistant = OpenAiAssistantFactory()
        if versioned_assistant_linked:
            assistant = assistant.create_new_version()

        pipeline = PipelineFactory()
        NodeFactory(type=node_type, pipeline=pipeline, params={"assistant_id": str(assistant.id)})
        assert pipeline.node_set.filter(type=node_type).exists()

        pipeline.create_new_version()

        original_node = pipeline.node_set.get(type=node_type)
        node_version = pipeline.versions.first().node_set.get(type=node_type)
        assistant_version = assistant if versioned_assistant_linked else assistant.versions.first()

        original_node_assistant_id = original_node.params["assistant_id"]
        node_version_assistant_id = node_version.params["assistant_id"]

        if versioned_assistant_linked:
            assert original_node_assistant_id == node_version_assistant_id == str(assistant.id)
        else:
            assert original_node_assistant_id != node_version_assistant_id
            assert original_node_assistant_id == str(assistant.id)
            assert node_version_assistant_id == str(assistant_version.id)

    def test_version_llm_with_prompt_node_with_collection(self):
        node_type = LLMResponseWithPrompt.__name__
        collection = CollectionFactory()
        pipeline = PipelineFactory()
        node = NodeFactory(type=node_type, pipeline=pipeline, params={"collection_id": str(collection.id)})

        # Versioning it should version the collection as well
        pipeline.create_new_version()

        # Versioning it without changes to the collection should not version the collection
        pipeline.create_new_version()
        assert node.versions.first().params["collection_id"] == str(collection.versions.first().id)
        assert node.versions.last().params["collection_id"] == str(collection.versions.first().id)

    def test_version_llm_with_prompt_node_with_source_material(self):
        node_type = LLMResponseWithPrompt.__name__
        source_material = SourceMaterialFactory()
        pipeline = PipelineFactory()
        NodeFactory(type=node_type, pipeline=pipeline, params={"source_material_id": str(source_material.id)})

        pipeline_version = pipeline.create_new_version()
        source_material_version = source_material.latest_version

        assert pipeline_version.node_set.count() == 3
        node_version = pipeline_version.node_set.filter(type=node_type).first()
        assert node_version.params["source_material_id"] == str(source_material_version.id)


@pytest.mark.django_db()
class TestArchivingNodes:
    @patch("apps.assistants.sync.push_assistant_to_openai", Mock())
    def test_archive_assistant_node(self):
        assistant = OpenAiAssistantFactory()
        pipeline = PipelineFactory()
        node = NodeFactory(type=AssistantNode.__name__, pipeline=pipeline, params={"assistant_id": str(assistant.id)})
        node_version = node.create_new_version()

        # Archiving the working version should not archive the assistant
        node.archive()
        assistant.refresh_from_db()
        assert assistant.is_archived is False

        # Archiving the working version should archive the assistant
        node_version.archive()
        assert OpenAiAssistant.objects.get_all().filter(working_version_id=assistant.id, is_archived=True).exists()

    def test_archive_llm_response_with_prompt_node(self):
        """
        Archiving this node should archive the related collection as well when this node is a version and the collection
        exists
        """
        collection = CollectionFactory()
        pipeline = PipelineFactory()
        node = NodeFactory(
            type=LLMResponseWithPrompt.__name__, pipeline=pipeline, params={"collection_id": str(collection.id)}
        )
        version_with_instance = node.create_new_version()
        node.params["collection_id"] = ""
        version_without_instance = node.create_new_version()

        # Archiving the working version should not archive the collection
        node.archive()
        collection.refresh_from_db()
        assert collection.is_archived is False

        # Archiving this version should archive the collection
        version_with_instance.archive()
        assert Collection.objects.get_all().filter(working_version_id=collection.id, is_archived=True).exists()

        # Archiving this version should not error
        version_without_instance.archive()


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
            source_material_id=1,
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
            [("system", "Help the user. User data: {'name': 'Anonymous'}. Source material: "), ("human", user_input)],
        ]
        assert [
            [(message.type, message.content) for message in call] for call in llm.get_call_messages()
        ] == expected_call_messages

        assert ExperimentSession.objects.count() == 0

    @pytest.mark.django_db()
    def test_archive_pipeline(self):
        assistant = OpenAiAssistantFactory()
        pipeline = PipelineFactory()
        NodeFactory(pipeline=pipeline, type="AssistantNode", params={"assistant_id": assistant.id})
        start_pipeline__action = EventActionFactory(
            action_type=EventActionType.PIPELINE_START,
            params={
                "pipeline_id": pipeline.id,
            },
        )
        experiment1 = ExperimentFactory()
        experiment2 = ExperimentFactory()
        static_trigger = StaticTriggerFactory(experiment=experiment2, action=start_pipeline__action)

        # Experiment and Static trigger still uses it
        assert pipeline.archive() is False

        experiment1.archive()
        # Static trigger from experiment2 still uses it
        assert pipeline.archive() is False

        static_trigger.archive()
        # Nothing uses it, so archive it
        assert pipeline.archive() is True

        # Double check that the node didn't archive the assistant
        assistant.refresh_from_db()
        assert assistant.is_archived is False

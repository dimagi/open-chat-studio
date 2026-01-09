from unittest import mock
from unittest.mock import Mock, patch

import pytest

from apps.channels.models import ExperimentChannel
from apps.chat.bots import PipelineTestBot
from apps.events.models import EventActionType
from apps.experiments.models import Experiment, ExperimentSession, Participant
from apps.pipelines.nodes.nodes import AssistantNode, LLMResponseWithPrompt
from apps.pipelines.tests.utils import (
    boolean_node,
    create_runnable,
    end_node,
    llm_response_with_prompt_node,
    render_template_node,
    start_node,
)
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

    @pytest.mark.parametrize("is_index", [True, False])
    def test_version_llm_with_prompt_node_with_collection(self, is_index):
        node_type = LLMResponseWithPrompt.__name__
        collection = CollectionFactory(is_index=is_index)
        pipeline = PipelineFactory()
        if is_index:
            param_name = "collection_index_ids"
            param_value = [collection.id]
        else:
            param_name = "collection_id"
            param_value = str(collection.id)
        node = NodeFactory(type=node_type, pipeline=pipeline, params={param_name: param_value})

        # Versioning it should version the collection as well
        pipeline.create_new_version()

        # Versioning it without changes to the collection should not version the collection
        pipeline.create_new_version()
        if is_index:
            assert node.versions.first().params[param_name] == [collection.versions.first().id]
            assert node.versions.last().params[param_name] == [collection.versions.first().id]
        else:
            assert node.versions.first().params[param_name] == str(collection.versions.first().id)
            assert node.versions.last().params[param_name] == str(collection.versions.first().id)

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

    def test_version_llm_with_prompt_node_with_multiple_dependencies(self):
        """
        Test that LLMResponseWithPrompt node properly handles versioning of multiple dependent resources.

        When a resource is versioned, it should create versions of only those dependencies that have changed.
        If a dependency has not changed, it should attach the latest version of that dependency to the new node version.
        """
        node_type = LLMResponseWithPrompt.__name__
        collection = CollectionFactory()
        collection_index = CollectionFactory(is_index=True)
        source_material = SourceMaterialFactory()

        # Create pipeline with node that has all three dependencies
        pipeline = PipelineFactory()
        NodeFactory(
            type=node_type,
            pipeline=pipeline,
            params={
                "collection_id": str(collection.id),
                "collection_index_ids": [str(collection_index.id)],
                "source_material_id": str(source_material.id),
            },
        )

        # First versioning - should create versions of all dependencies
        pipeline_version = pipeline.create_new_version()
        collection_version = collection.latest_version
        collection_index_version = collection_index.latest_version
        source_material_version = source_material.latest_version

        node_version = pipeline_version.node_set.get(type=node_type)
        assert node_version.params["collection_id"] == str(collection_version.id)
        assert node_version.params["collection_index_ids"] == [collection_index_version.id]
        assert node_version.params["source_material_id"] == str(source_material_version.id)

        # Second versioning without changes - should reuse existing dependency versions
        pipeline_version_2 = pipeline.create_new_version()

        node_version_2 = pipeline_version_2.node_set.get(type=node_type)
        assert node_version_2.params["collection_id"] == str(collection_version.id)
        assert node_version_2.params["collection_index_ids"] == [collection_index_version.id]
        assert node_version_2.params["source_material_id"] == str(source_material_version.id)


@pytest.mark.django_db()
class TestArchivingNodes:
    @patch("apps.pipelines.models.Node._archive_related_params")
    def test_archive_related_objects_conditionally(self, archive_related_params):
        """Related objects should only be archived when the node is a version"""
        pipeline = PipelineFactory()
        node = NodeFactory(pipeline=pipeline)
        node_version = NodeFactory(pipeline=pipeline, working_version=node)

        node.archive()
        archive_related_params.assert_not_called()

        node_version.archive()
        archive_related_params.assert_called()

    @patch("apps.assistants.sync.push_assistant_to_openai", Mock())
    @mock.patch("apps.service_providers.models.LlmProvider.create_remote_index")
    def test_archive_related_objects(self, create_remote_index):
        # Setup related objects
        assistant = OpenAiAssistantFactory()
        collection = CollectionFactory()
        collection_index = CollectionFactory(
            is_index=True, openai_vector_store_id="v-123", llm_provider=LlmProviderFactory()
        )

        # Setup mocks
        create_remote_index.return_value = "v-456"

        # Build the pipeline
        pipeline = PipelineFactory()
        NodeFactory(type=AssistantNode.__name__, pipeline=pipeline, params={"assistant_id": str(assistant.id)})
        NodeFactory(
            type=LLMResponseWithPrompt.__name__,
            pipeline=pipeline,
            params={
                "collection_id": str(collection.id),
                "collection_index_ids": [str(collection_index.id)],
            },
        )
        pipeline.create_new_version()

        assistant_version = assistant.versions.first()
        collection_version = collection.versions.first()
        collection_index_version = collection_index.versions.first()

        pipeline.archive()

        # Ensure that the working versions are not archived, but the versions of each related object are
        assistant.refresh_from_db()
        collection.refresh_from_db()
        collection_index.refresh_from_db()

        assistant_version.refresh_from_db()
        collection_version.refresh_from_db()
        collection_index_version.refresh_from_db()

        assert assistant.is_archived is False
        assert collection.is_archived is False
        assert collection_index.is_archived is False

        assert assistant_version.is_archived is True
        assert collection_version.is_archived is True
        assert collection_index_version.is_archived is True


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
            assert model.objects.filter(team=team).count() == 0

        bot = PipelineTestBot(pipeline=pipeline, user_id=requesting_user.id)
        bot.process_input("test")

        for model in temporary_instance_models:
            assert model.objects.filter(team=team).count() == 0

        if participant_exists:
            assert Participant.objects.filter(team=team, user=requesting_user).exists()

    @django_db_with_data()
    @mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
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
        bot = PipelineTestBot(pipeline=pipeline, user_id=user.id)
        bot.process_input(user_input)
        expected_call_messages = [
            [
                ("system", "Help the user. User data: {'name': 'Anonymous'}. Source material: "),
                ("human", [{"text": user_input, "type": "text"}]),
            ],
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


@pytest.mark.django_db()
class TestPipelineValidation:
    def test_validate_basic(self):
        start = start_node()
        router = boolean_node()
        template = render_template_node("T: {{ input }}")
        end = end_node()
        nodes = [start, router, template, end]

        edges = [
            {
                "id": "start -> router",
                "source": start["id"],
                "target": router["id"],
            },
            {
                "id": "router -> template",
                "source": router["id"],
                "target": template["id"],
                "sourceHandle": "output_1",
            },
            {
                "id": "template -> end",
                "source": template["id"],
                "target": end["id"],
            },
            {
                "id": "router -> end",
                "source": router["id"],
                "target": end["id"],
                "sourceHandle": "output_0",
            },
        ]
        flow_nodes = []
        for node in nodes:
            flow_nodes.append({"id": node["id"], "data": node})

        pipeline = PipelineFactory()
        pipeline.data = {"edges": edges, "nodes": flow_nodes}
        pipeline.update_nodes_from_data()
        errors = pipeline.validate()
        assert not errors

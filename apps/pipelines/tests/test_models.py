from unittest import mock
from unittest.mock import Mock, patch

import pytest

from apps.channels.models import ExperimentChannel
from apps.chat.bots import PipelineTestBot
from apps.documents.models import CollectionFile
from apps.events.models import EventActionType
from apps.experiments.models import Experiment, ExperimentSession, Participant
from apps.pipelines.flow import split_flow_data
from apps.pipelines.models import Node, Pipeline
from apps.pipelines.nodes.nodes import AssistantNode, LLMResponseWithPrompt
from apps.pipelines.repository import ORMRepository
from apps.pipelines.tests.utils import (
    assistant_node,
    boolean_node,
    create_pipeline_model,
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
from apps.utils.factories.files import FileFactory
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory
from apps.utils.factories.service_provider_factories import (
    LlmProviderFactory,
    LlmProviderModelFactory,
)
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory
from apps.utils.tests.langchain import (
    FakeLlmEcho,
    build_fake_llm_service,
)


@pytest.mark.django_db()
def test_archive_pipeline_archives_nodes_as_well():
    pipeline = PipelineFactory.create()
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
        assistant = OpenAiAssistantFactory.create()
        if versioned_assistant_linked:
            assistant = assistant.create_new_version()

        pipeline = PipelineFactory.create()
        NodeFactory.create(type=node_type, pipeline=pipeline, params={"assistant_id": str(assistant.id)})
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
        collection = CollectionFactory.create(is_index=is_index)
        pipeline = PipelineFactory.create()
        if is_index:
            param_name = "collection_index_ids"
            param_value = [collection.id]
        else:
            param_name = "collection_id"
            param_value = str(collection.id)
        node = NodeFactory.create(type=node_type, pipeline=pipeline, params={param_name: param_value})

        pipeline.create_new_version()
        pipeline.create_new_version()

        if is_index:
            # ADR-0031: index collections are live shared resources. Publishing keeps the working
            # id verbatim and does NOT create a collection version.
            assert not collection.versions.exists()
            assert node.versions.first().params[param_name] == [collection.id]
            assert node.versions.last().params[param_name] == [collection.id]
        else:
            # ADR-0031: media collections are also live shared resources — not versioned per bot.
            assert not collection.versions.exists()
            assert node.versions.first().params[param_name] == str(collection.id)
            assert node.versions.last().params[param_name] == str(collection.id)

    def test_version_llm_with_prompt_node_with_source_material(self):
        node_type = LLMResponseWithPrompt.__name__
        source_material = SourceMaterialFactory.create()
        pipeline = PipelineFactory.create()
        NodeFactory.create(type=node_type, pipeline=pipeline, params={"source_material_id": str(source_material.id)})

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
        collection = CollectionFactory.create()
        collection_index = CollectionFactory.create(is_index=True)
        source_material = SourceMaterialFactory.create()

        # Create pipeline with node that has all three dependencies
        pipeline = PipelineFactory.create()
        NodeFactory.create(
            type=node_type,
            pipeline=pipeline,
            params={
                "collection_id": str(collection.id),
                "collection_index_ids": [str(collection_index.id)],
                "source_material_id": str(source_material.id),
            },
        )

        # First versioning - only source material versions; collections stay live (ADR-0031).
        pipeline_version = pipeline.create_new_version()
        source_material_version = source_material.latest_version

        node_version = pipeline_version.node_set.get(type=node_type)
        assert node_version.params["collection_id"] == str(collection.id)
        assert node_version.params["collection_index_ids"] == [str(collection_index.id)]
        assert node_version.params["source_material_id"] == str(source_material_version.id)
        assert not collection.versions.exists()
        assert not collection_index.versions.exists()

        # Second versioning - reuse source-material version; collections still live.
        pipeline_version_2 = pipeline.create_new_version()

        node_version_2 = pipeline_version_2.node_set.get(type=node_type)
        assert node_version_2.params["collection_id"] == str(collection.id)
        assert node_version_2.params["collection_index_ids"] == [str(collection_index.id)]
        assert node_version_2.params["source_material_id"] == str(source_material_version.id)

    def test_published_node_resolves_live_index(self):
        """A bot published after ADR-0031 references the live working index, not a frozen copy."""
        node_type = LLMResponseWithPrompt.__name__
        collection_index = CollectionFactory.create(is_index=True)
        pipeline = PipelineFactory.create()
        NodeFactory.create(type=node_type, pipeline=pipeline, params={"collection_index_ids": [collection_index.id]})
        pipeline_version = pipeline.create_new_version()

        node_version = pipeline_version.node_set.get(type=node_type)
        # End-to-end: publishing keeps the working id (precondition), and runtime lookup resolves it to the live
        # working index.
        published_ids = node_version.params["collection_index_ids"]
        assert published_ids == [collection_index.id]

        resolved = ORMRepository().get_collections_for_search(published_ids)
        assert [c.id for c in resolved] == [collection_index.id]
        assert resolved[0].is_working_version

    def test_index_content_change_does_not_mark_node_dirty(self):
        """ADR-0031: index content drift must not flag a published bot as having unpublished changes."""
        node_type = LLMResponseWithPrompt.__name__
        collection_index = CollectionFactory.create(is_index=True)
        pipeline = PipelineFactory.create()
        node = NodeFactory.create(
            type=node_type, pipeline=pipeline, params={"collection_index_ids": [collection_index.id]}
        )
        pipeline.create_new_version()

        # Simulate a document-source sync adding a file to the working index.
        file = FileFactory.create(team=collection_index.team)
        CollectionFile.objects.create(file=file, collection=collection_index, document_source=None)

        node.refresh_from_db()
        assert not node.compare_with_latest()

    def test_media_content_change_does_not_mark_node_dirty(self):
        """ADR-0031: media collection content drift must not flag a published bot as dirty."""
        node_type = LLMResponseWithPrompt.__name__
        media_collection = CollectionFactory.create(is_index=False)
        pipeline = PipelineFactory.create()
        node = NodeFactory.create(type=node_type, pipeline=pipeline, params={"collection_id": str(media_collection.id)})
        pipeline.create_new_version()

        # Simulate a manual edit adding a file to the working media collection.
        file = FileFactory.create(team=media_collection.team)
        CollectionFile.objects.create(file=file, collection=media_collection, document_source=None)

        node.refresh_from_db()
        assert not node.compare_with_latest()


@pytest.mark.django_db()
class TestArchivingNodes:
    @patch("apps.pipelines.models.Node._archive_related_params")
    def test_archive_related_objects_conditionally(self, archive_related_params):
        """Related objects should only be archived when the node is a version"""
        pipeline = PipelineFactory.create()
        node = NodeFactory.create(pipeline=pipeline)
        node_version = NodeFactory.create(pipeline=pipeline, working_version=node)

        node.archive()
        archive_related_params.assert_not_called()

        node_version.archive()
        archive_related_params.assert_called()

    @patch("apps.assistants.sync.push_assistant_to_openai", Mock())
    def test_archive_related_objects(self):
        # Setup related objects
        assistant = OpenAiAssistantFactory.create()
        collection = CollectionFactory.create()
        collection_index = CollectionFactory.create(
            is_index=True, openai_vector_store_id="v-123", llm_provider=LlmProviderFactory.create()
        )

        # Build the pipeline
        pipeline = PipelineFactory.create()
        NodeFactory.create(type=AssistantNode.__name__, pipeline=pipeline, params={"assistant_id": str(assistant.id)})
        NodeFactory.create(
            type=LLMResponseWithPrompt.__name__,
            pipeline=pipeline,
            params={
                "collection_id": str(collection.id),
                "collection_index_ids": [str(collection_index.id)],
            },
        )
        pipeline.create_new_version()

        assistant_version = assistant.versions.first()

        pipeline.archive()

        # Ensure that the working versions are not archived
        assistant.refresh_from_db()
        collection.refresh_from_db()
        collection_index.refresh_from_db()
        assistant_version.refresh_from_db()

        assert assistant.is_archived is False
        # ADR-0031: media + index collections are live shared resources — never versioned per bot,
        # so the working collections are untouched and no collection versions exist.
        assert collection.is_archived is False
        assert not collection.versions.exists()
        assert collection_index.is_archived is False
        assert not collection_index.versions.exists()

        # Assistants are still versioned per bot and get archived.
        assert assistant_version.is_archived is True

    def test_archive_legacy_frozen_index_version(self):
        """
        LEGACY data: a pre-ADR-0031 node may reference both the live working index id and a
        frozen index version id in collection_index_ids. Archiving the pipeline must archive the
        frozen copy (is_a_version=True) while leaving the live working index (is_a_version=False)
        untouched. Including both ids makes the is_a_version guard load-bearing: dropping the guard
        would wrongly archive the working index and fail this test.
        """
        # Working index collection (is_a_version=False)
        collection_index = CollectionFactory.create(is_index=True)
        # Frozen version of the index (is_a_version=True) — simulates the legacy per-bot copy
        frozen_index = collection_index.create_new_version()
        assert frozen_index.is_a_version, "pre-condition: frozen_index must be a version"

        # Node references BOTH the live working id and the frozen version id (legacy stored data)
        pipeline = PipelineFactory.create()
        NodeFactory.create(
            type=LLMResponseWithPrompt.__name__,
            pipeline=pipeline,
            params={"collection_index_ids": [collection_index.id, frozen_index.id]},
        )

        # Version the pipeline — the version node copies the param verbatim
        pipeline.create_new_version()

        # Archive the pipeline (working version → archives all version nodes too)
        pipeline.archive()

        frozen_index.refresh_from_db()
        collection_index.refresh_from_db()
        # The frozen per-bot copy is archived (legacy cleanup branch)
        assert frozen_index.is_archived is True
        # The live working index is protected by the is_a_version guard
        assert collection_index.is_archived is False

    def test_archive_legacy_frozen_media_version(self):
        """
        LEGACY data: a pre-ADR-0031 node may reference a frozen media collection version in
        collection_id. Archiving the pipeline must archive that frozen copy (is_a_version=True)
        while leaving a live working media collection (referenced by another node) untouched.
        The working-media node makes the is_a_version guard load-bearing: dropping the guard would
        wrongly archive the working media and fail this test.
        """
        node_type = LLMResponseWithPrompt.__name__
        working_media = CollectionFactory.create(is_index=False)
        frozen_media = working_media.create_new_version()
        assert frozen_media.is_a_version, "pre-condition: frozen_media must be a version"

        pipeline = PipelineFactory.create()
        # Node A references the live working media id; Node B references the frozen version id.
        NodeFactory.create(type=node_type, pipeline=pipeline, params={"collection_id": str(working_media.id)})
        NodeFactory.create(type=node_type, pipeline=pipeline, params={"collection_id": str(frozen_media.id)})

        pipeline.create_new_version()
        pipeline.archive()

        working_media.refresh_from_db()
        frozen_media.refresh_from_db()
        assert frozen_media.is_archived is True
        assert working_media.is_archived is False


class TestPipeline:
    @pytest.mark.django_db()
    @pytest.mark.parametrize("participant_exists", [True, False])
    def test_simple_invoke(self, participant_exists, team_with_users):
        """Test that the mock data is not being persisted when doing a simple invoke"""
        team = team_with_users
        requesting_user = team_with_users.members.first()
        pipeline = PipelineFactory.create(team=team)
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

    @pytest.mark.django_db()
    @mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
    def test_simple_invoke_with_pipeline(self, get_llm_service):
        """Test simple invoke with a pipeline that has an LLM node"""
        provider = LlmProviderFactory.create()
        provider_model = LlmProviderModelFactory.create()
        source_material = SourceMaterialFactory.create()
        llm = FakeLlmEcho()
        service = build_fake_llm_service(None, llm)
        get_llm_service.return_value = service

        llm_node = llm_response_with_prompt_node(
            str(provider.id),
            str(provider_model.id),
            source_material_id=str(source_material.id),
            prompt="Help the user. User data: {participant_data}. Source material: {source_material}",
            history_type="global",
        )
        nodes = [start_node(), llm_node, end_node()]
        pipeline = PipelineFactory.create()
        create_runnable(pipeline, nodes)
        pipeline.save()

        user_input = "The User Input"
        user = UserFactory.create()
        bot = PipelineTestBot(pipeline=pipeline, user_id=user.id)
        bot.process_input(user_input)
        expected_call_messages = [
            [
                ("system", "Help the user. User data: {'name': 'Anonymous'}. Source material: material"),
                ("human", [{"text": user_input, "type": "text"}]),
            ],
        ]
        assert [
            [(message.type, message.content) for message in call] for call in llm.get_call_messages()
        ] == expected_call_messages

        assert ExperimentSession.objects.count() == 0

    @pytest.mark.django_db()
    def test_archive_pipeline(self):
        assistant = OpenAiAssistantFactory.create()
        pipeline = PipelineFactory.create()
        NodeFactory.create(pipeline=pipeline, type="AssistantNode", params={"assistant_id": assistant.id})
        start_pipeline__action = EventActionFactory.create(
            action_type=EventActionType.PIPELINE_START,
            params={
                "pipeline_id": pipeline.id,
            },
        )
        experiment1 = ExperimentFactory.create()
        experiment2 = ExperimentFactory.create()
        static_trigger = StaticTriggerFactory.create(experiment=experiment2, action=start_pipeline__action)

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
class TestUpdateNodesFromData:
    def test_node_content_comes_from_the_mapping_not_from_data(self):
        """Layout-only data plus a node_data mapping creates rows with the mapped content."""
        pipeline = PipelineFactory.create()
        pipeline.data = {
            "edges": [],
            "nodes": [
                {"id": "start", "type": "startNode", "position": {"x": 0, "y": 0}},
                {"id": "template-1", "type": "pipelineNode", "position": {"x": 10, "y": 0}},
                {"id": "end", "type": "endNode", "position": {"x": 20, "y": 0}},
            ],
        }
        pipeline.update_nodes_from_data(
            {
                "start": {"type": "StartNode", "label": "", "params": {"name": "start"}},
                "template-1": {
                    "type": "RenderTemplate",
                    "label": "Template",
                    "params": {"name": "template-1", "template_string": "{{ input }}"},
                },
                "end": {"type": "EndNode", "label": "", "params": {"name": "end"}},
            }
        )

        node = Node.objects.get(pipeline=pipeline, flow_id="template-1")
        assert node.type == "RenderTemplate"
        assert node.label == "Template"
        assert node.params["template_string"] == "{{ input }}"

    def test_position_is_shadow_written_to_the_row(self):
        """A mapping entry's position lands on the row's position columns (floats kept
        verbatim); the layout in ``Pipeline.data`` stays authoritative for reads for now."""
        pipeline = PipelineFactory.create()
        pipeline.data = {"edges": [], "nodes": [{"id": "n1", "type": "startNode", "position": {"x": 10.7, "y": -3.2}}]}
        pipeline.update_nodes_from_data(
            {"n1": {"type": "StartNode", "params": {"name": "start"}, "position": {"x": 10.7, "y": -3.2}}}
        )

        node = Node.objects.get(pipeline=pipeline, flow_id="n1")
        assert node.position_x == 10.7
        assert node.position_y == -3.2
        assert node.position == {"x": 10.7, "y": -3.2}

    @pytest.mark.parametrize(
        "position",
        [
            pytest.param(None, id="absent"),
            pytest.param({"x": "abc", "y": 2}, id="non-numeric"),
            pytest.param({"x": 1}, id="missing-axis"),
            pytest.param({}, id="empty"),
        ],
    )
    def test_unusable_position_is_not_written(self, position):
        """Raw import files bypass wire validation; a bad position must not crash the
        save or write garbage — the row keeps its previous position columns."""
        pipeline = PipelineFactory.create()
        pipeline.data = {"edges": [], "nodes": [{"id": "n1", "type": "startNode"}]}
        pipeline.update_nodes_from_data(
            {"n1": {"type": "StartNode", "params": {"name": "start"}, "position": position}}
        )

        node = Node.objects.get(pipeline=pipeline, flow_id="n1")
        assert node.position is None

    def test_nodes_absent_from_the_mapping_are_left_untouched(self):
        """PATCH saves only carry changed nodes; existing rows keep their content."""
        start, template, end = start_node(), render_template_node(), end_node()
        pipeline = create_pipeline_model([start, template, end])
        row = Node.objects.get(pipeline=pipeline, flow_id=template["id"])
        original_params = row.params

        pipeline.update_nodes_from_data({})

        row.refresh_from_db()
        assert row.params == original_params
        assert Node.objects.filter(pipeline=pipeline).count() == 3

    def test_unknown_node_without_mapping_entry_raises(self):
        """A graph node with neither a mapping entry nor an existing row is an error."""
        pipeline = PipelineFactory.create()
        pipeline.data["nodes"].append({"id": "ghost", "type": "pipelineNode"})

        with pytest.raises(ValueError, match="ghost"):
            pipeline.update_nodes_from_data({})

    def test_re_adding_archived_node_flow_id_creates_fresh_working_node(self):
        """Removing a node that has versions archives it; revert re-introduces the same
        flow_id, which must yield a fresh editable working node without colliding with
        the archived row."""
        start, template, end = start_node(), render_template_node(), end_node()
        pipeline = create_pipeline_model([start, template, end])
        pipeline.create_new_version()  # the template node now has a version

        original = Node.objects.get(pipeline=pipeline, flow_id=template["id"])
        node_version = original.versions.get()

        def set_nodes(node_dicts):
            data = {"edges": [], "nodes": [{"id": n["id"], "data": n} for n in node_dicts]}
            pipeline.data, node_data = split_flow_data(data)
            pipeline.update_nodes_from_data(node_data)

        # remove the template node; it has a version so it is archived rather than deleted
        set_nodes([start, end])
        original.refresh_from_db()
        assert original.is_archived

        # re-add the same flow_id
        set_nodes([start, template, end])

        re_added = Node.objects.get(pipeline=pipeline, flow_id=template["id"])
        assert re_added.id != original.id
        assert re_added.is_working_version
        assert not re_added.is_archived
        assert re_added.params["template_string"] == template["params"]["template_string"]

        # the archived node and its version history are untouched
        original.refresh_from_db()
        node_version.refresh_from_db()
        assert original.is_archived
        assert node_version.working_version_id == original.id
        assert Node.objects.get_all().filter(pipeline=pipeline, flow_id=template["id"]).count() == 2

        # the re-added node is editable in place; no duplicate row appears
        template["params"]["template_string"] = "updated: {{ input }}"
        set_nodes([start, template, end])
        re_added.refresh_from_db()
        assert re_added.params["template_string"] == "updated: {{ input }}"
        assert Node.objects.filter(pipeline=pipeline, flow_id=template["id"]).count() == 1

        # publishing again versions the re-added node, not the archived one
        pipeline.create_new_version()
        assert re_added.versions.count() == 1
        assert original.versions.count() == 1


@pytest.mark.django_db()
class TestLayoutOnlyData:
    def test_create_default_stores_layout_only_data(self):
        pipeline = Pipeline.create_default(TeamFactory())

        assert all(set(node) <= {"id", "type", "position"} for node in pipeline.data["nodes"])
        names = {node.params["name"] for node in pipeline.node_set.all()}
        assert names == {"start", "end"}

    def test_copy_keeps_readable_flow_ids_and_layout_only_data(self):
        start, template, end = start_node(), render_template_node(), end_node()
        pipeline = create_pipeline_model([start, template, end])

        copy = pipeline.create_new_version(is_copy=True)

        assert all(set(node) <= {"id", "type", "position"} for node in copy.data["nodes"])
        copied_template = copy.node_set.get(type="RenderTemplate")
        assert copied_template.flow_id.startswith("RenderTemplate-")
        assert copied_template.flow_id != template["id"]
        assert copied_template.params["template_string"] == template["params"]["template_string"]
        # edges follow the remapped ids
        data_node_ids = {node["id"] for node in copy.data["nodes"]}
        for edge in copy.data["edges"]:
            assert edge["source"] in data_node_ids
            assert edge["target"] in data_node_ids

    def test_data_without_positions_serves_node_content_from_rows(self):
        start, template, end = start_node(), render_template_node(), end_node()
        pipeline = create_pipeline_model([start, template, end])
        row = Node.objects.get(pipeline=pipeline, flow_id=template["id"])
        row.set_params({**row.params, "template_string": "row wins: {{ input }}"})

        nodes_by_id = {node["id"]: node for node in pipeline.data_without_positions["nodes"]}

        template_node = nodes_by_id[template["id"]]
        assert template_node["data"]["params"]["template_string"] == "row wins: {{ input }}"
        assert "position" not in template_node


@pytest.mark.django_db()
class TestPipelineRevert:
    @patch("apps.assistants.sync.push_assistant_to_openai", Mock())
    def test_revert_remaps_versioned_params_back_to_working_records(self):
        """Reverting rebuilds the working pipeline from a version's data, remapping params that
        reference versioned records (assistant, source material) back to their working ids."""
        assistant = OpenAiAssistantFactory.create()
        source_material = SourceMaterialFactory.create(team=assistant.team)
        provider = LlmProviderFactory.create(team=assistant.team)
        provider_model = LlmProviderModelFactory.create(team=assistant.team)

        start, asst, llm, end = (
            start_node(),
            assistant_node(str(assistant.id)),
            llm_response_with_prompt_node(
                str(provider.id), str(provider_model.id), source_material_id=str(source_material.id)
            ),
            end_node(),
        )
        pipeline = create_pipeline_model([start, asst, llm, end])
        pipeline.save(update_fields=["data"])
        version = pipeline.create_new_version()

        # On publish, the version's node params point at the versioned records.
        version_asst = version.node_set.get(type=AssistantNode.__name__)
        assert version_asst.params["assistant_id"] == str(assistant.latest_version.id)
        version_llm = version.node_set.get(type=LLMResponseWithPrompt.__name__)
        assert version_llm.params["source_material_id"] == str(source_material.latest_version.id)

        # Edit the working pipeline so revert has something to undo.
        other_assistant = OpenAiAssistantFactory.create(team=assistant.team)
        asst["params"]["assistant_id"] = str(other_assistant.id)
        create_pipeline_model([start, asst, llm, end], pipeline=pipeline)

        pipeline.revert_to_version(version)

        working_asst = pipeline.node_set.get(type=AssistantNode.__name__)
        working_llm = pipeline.node_set.get(type=LLMResponseWithPrompt.__name__)
        # Params point at the working records, not the versioned ones from the snapshot.
        assert working_asst.params["assistant_id"] == str(assistant.id)
        assert working_llm.params["source_material_id"] == str(source_material.id)
        # The mirrored resource FK columns are re-synced to the working records too.
        assert working_asst.assistant_id == assistant.id
        assert working_llm.source_material_id == source_material.id

        # The version's nodes are untouched by the revert.
        version_asst.refresh_from_db()
        assert version_asst.params["assistant_id"] == str(assistant.latest_version.id)
        assert version_asst.assistant_id == assistant.latest_version.id

    def test_revert_from_version_with_old_format_data(self):
        """Version rows created before ADR-0046 (or skipped by the migration's drift guard)
        still embed node blobs in their data. Revert must work against them, taking
        content from the version's node rows and persisting layout-only data."""
        start, template, end = start_node(), render_template_node(), end_node()
        pipeline = create_pipeline_model([start, template, end])
        version = pipeline.create_new_version()

        # Simulate a pre-migration version row: old-format data with a stale blob.
        version.data = {
            "edges": [],
            "nodes": [
                {"id": n["id"], "data": {**n, "params": dict(n.get("params", {}), name="stale")}}
                for n in [start, template, end]
            ],
        }
        version.save(update_fields=["data"])

        pipeline.revert_to_version(version)

        assert all("data" not in node for node in pipeline.data["nodes"])
        working_template = pipeline.node_set.get(type="RenderTemplate")
        version_template = version.node_set.get(type="RenderTemplate")
        assert working_template.params == version_template.params


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

        pipeline = PipelineFactory.create()
        pipeline.data, node_data = split_flow_data({"edges": edges, "nodes": flow_nodes})
        pipeline.update_nodes_from_data(node_data)
        errors = pipeline.validate()
        assert not errors


@pytest.mark.parametrize(
    ("node_type", "param_name", "expected"),
    [
        pytest.param(LLMResponseWithPrompt.__name__, "llm_provider_id", True, id="declared"),
        pytest.param(LLMResponseWithPrompt.__name__, "assistant_id", False, id="not-declared"),
        pytest.param(AssistantNode.__name__, "assistant_id", True, id="declared-on-other-type"),
        pytest.param("NoSuchNode", "assistant_id", False, id="unknown-node-type"),
    ],
)
def test_node_has_parameter(node_type, param_name, expected):
    assert Node(type=node_type).has_parameter(param_name) is expected

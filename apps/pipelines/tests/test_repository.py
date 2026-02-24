"""Tests for InMemoryPipelineRepository.

Every test in this module runs WITHOUT @pytest.mark.django_db, demonstrating
that InMemoryPipelineRepository enables fully DB-free pipeline testing.
"""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock

from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.nodes.context import NodeContext
from apps.pipelines.nodes.nodes import CodeNode, RenderTemplate
from apps.pipelines.repository import InMemoryPipelineRepository

# ===========================================================================
# Helpers
# ===========================================================================


def _mock_session(*, session_id=1, experiment_id=10, team_id=100, global_data=None):
    """Build a lightweight mock that looks enough like an ExperimentSession."""
    session = MagicMock()
    session.id = session_id
    session.experiment_id = experiment_id
    session.team_id = team_id
    session.team.id = team_id
    session.participant = SimpleNamespace(identifier="test-user", platform="web", global_data=global_data or {})
    session.chat = MagicMock()
    return session


def _collection(*, name: str, summary: str, is_index: bool = True) -> dict:
    """Build a dict-based collection stub."""
    return {"name": name, "summary": summary, "is_index": is_index}


# ===========================================================================
# Store and retrieve tests
# ===========================================================================


class TestStoreAndRetrieve:
    def test_store_and_retrieve_llm_provider(self):
        provider = SimpleNamespace(id=1, name="openai")
        repo = InMemoryPipelineRepository(llm_providers={1: provider})
        assert repo.get_llm_provider(1) is provider

    def test_store_and_retrieve_source_material(self):
        material = SimpleNamespace(id=5, content="Some source material text")
        repo = InMemoryPipelineRepository(source_materials={5: material})
        assert repo.get_source_material(5) is material

    def test_store_and_retrieve_collection(self):
        collection = _collection(name="FAQ", summary="Frequently asked questions")
        repo = InMemoryPipelineRepository(collections={3: collection})
        assert repo.get_collection(3) is collection

    def test_store_and_retrieve_assistant(self):
        assistant = SimpleNamespace(id=7, name="helper-bot")
        repo = InMemoryPipelineRepository(assistants={7: assistant})
        assert repo.get_assistant(7) is assistant


# ===========================================================================
# Recording tests
# ===========================================================================


class TestRecording:
    def test_create_file_records_in_files_created(self):
        repo = InMemoryPipelineRepository()
        file_obj = BytesIO(b"hello world")
        result = repo.create_file(
            filename="test.txt",
            file_obj=file_obj,
            team_id=1,
            content_type="text/plain",
            purpose="attachment",
        )
        assert len(repo.files_created) == 1
        record = repo.files_created[0]
        assert record.filename == "test.txt"
        assert record.team_id == 1
        assert record.content_type == "text/plain"
        assert record.purpose == "attachment"
        assert record.id == 1
        # The returned value supports attribute access (like a Django model)
        assert result is record
        assert result.id == 1

    def test_create_file_increments_ids(self):
        repo = InMemoryPipelineRepository()
        repo.create_file("a.txt", BytesIO(b"a"), team_id=1)
        repo.create_file("b.txt", BytesIO(b"b"), team_id=1)
        assert repo.files_created[0].id == 1
        assert repo.files_created[1].id == 2

    def test_attach_files_to_chat_records_in_attached_files(self):
        repo = InMemoryPipelineRepository()
        mock_chat = MagicMock()
        fake_files = [{"id": 1}, {"id": 2}]
        repo.attach_files_to_chat(chat=mock_chat, attachment_type="code_interpreter", files=fake_files)
        assert len(repo.attached_files) == 1
        record = repo.attached_files[0]
        assert record["chat"] is mock_chat
        assert record["attachment_type"] == "code_interpreter"
        assert record["files"] == fake_files

    def test_save_pipeline_chat_message_records_in_saved_messages(self):
        repo = InMemoryPipelineRepository()
        history = {"type": "node", "name": "llm_1"}
        result = repo.save_pipeline_chat_message(
            history=history,
            node_id="node-123",
            human_message="What is AI?",
            ai_message="AI is ...",
        )
        assert len(repo.saved_messages) == 1
        msg = repo.saved_messages[0]
        assert msg["history"] is history
        assert msg["node_id"] == "node-123"
        assert msg["human_message"] == "What is AI?"
        assert msg["ai_message"] == "AI is ..."
        assert result is msg

    def test_save_compression_checkpoint_global_records(self):
        repo = InMemoryPipelineRepository()
        repo.save_compression_checkpoint_global(message_id=42, compression_marker="marker", history_mode="rolling")
        assert len(repo.compression_checkpoints) == 1
        cp = repo.compression_checkpoints[0]
        assert cp["type"] == "global"
        assert cp["message_id"] == 42

    def test_save_compression_checkpoint_pipeline_records(self):
        repo = InMemoryPipelineRepository()
        repo.save_compression_checkpoint_pipeline(message_id=99, compression_marker="summary", history_mode="full")
        assert len(repo.compression_checkpoints) == 1
        cp = repo.compression_checkpoints[0]
        assert cp["type"] == "pipeline"
        assert cp["message_id"] == 99

    def test_get_participant_schedules_records_in_schedule_lookups(self):
        repo = InMemoryPipelineRepository()
        participant = SimpleNamespace(identifier="p1")
        result = repo.get_participant_schedules(participant=participant, experiment_id=10)
        assert result == []
        assert len(repo.schedule_lookups) == 1
        lookup = repo.schedule_lookups[0]
        assert lookup["participant"] is participant
        assert lookup["experiment_id"] == 10


# ===========================================================================
# Error handling tests (return None for unconfigured lookups)
# ===========================================================================


class TestUnconfiguredLookups:
    """InMemoryPipelineRepository returns None for unconfigured IDs,
    matching the DjangoPipelineRepository's DoesNotExist -> None pattern."""

    def test_get_llm_provider_returns_none_for_unknown_id(self):
        repo = InMemoryPipelineRepository()
        assert repo.get_llm_provider(999) is None

    def test_get_source_material_returns_none_for_unknown_id(self):
        repo = InMemoryPipelineRepository()
        assert repo.get_source_material(999) is None

    def test_get_collection_returns_none_for_unknown_id(self):
        repo = InMemoryPipelineRepository()
        assert repo.get_collection(999) is None

    def test_get_assistant_returns_none_for_unknown_id(self):
        repo = InMemoryPipelineRepository()
        assert repo.get_assistant(999) is None


# ===========================================================================
# Chat history tests
# ===========================================================================


class TestChatHistory:
    def test_get_pipeline_chat_history_returns_preconfigured_history(self):
        history = {"type": "node", "name": "llm_1", "messages": []}
        repo = InMemoryPipelineRepository(chat_histories={(1, "node", "llm_1"): history})
        session = _mock_session(session_id=1)
        result = repo.get_pipeline_chat_history(session, "node", "llm_1")
        assert result is history

    def test_get_pipeline_chat_history_returns_none_for_missing(self):
        repo = InMemoryPipelineRepository()
        session = _mock_session(session_id=1)
        assert repo.get_pipeline_chat_history(session, "node", "llm_1") is None

    def test_get_or_create_creates_new_entry(self):
        repo = InMemoryPipelineRepository()
        session = _mock_session(session_id=1)
        history, created = repo.get_or_create_pipeline_chat_history(session, "node", "llm_1")
        assert created is True
        assert history["session_id"] == 1
        assert history["type"] == "node"
        assert history["name"] == "llm_1"
        assert history["messages"] == []

    def test_get_or_create_returns_existing_entry(self):
        existing = {"session_id": 1, "type": "node", "name": "llm_1", "messages": ["prior"]}
        repo = InMemoryPipelineRepository(chat_histories={(1, "node", "llm_1"): existing})
        session = _mock_session(session_id=1)
        history, created = repo.get_or_create_pipeline_chat_history(session, "node", "llm_1")
        assert created is False
        assert history is existing

    def test_get_or_create_second_call_returns_same_entry(self):
        repo = InMemoryPipelineRepository()
        session = _mock_session(session_id=1)
        first, created1 = repo.get_or_create_pipeline_chat_history(session, "node", "llm_1")
        second, created2 = repo.get_or_create_pipeline_chat_history(session, "node", "llm_1")
        assert created1 is True
        assert created2 is False
        assert first is second

    def test_get_session_messages_until_marker_returns_empty(self):
        repo = InMemoryPipelineRepository()
        result = repo.get_session_messages_until_marker(chat=MagicMock(), marker="test_marker")
        assert result == []


# ===========================================================================
# Collection search tests
# ===========================================================================


class TestCollectionSearch:
    def test_get_collections_for_search_returns_matching(self):
        coll_a = _collection(name="A", summary="Index A", is_index=True)
        coll_b = _collection(name="B", summary="Index B", is_index=True)
        coll_c = _collection(name="C", summary="Not indexed", is_index=False)
        repo = InMemoryPipelineRepository(collections={1: coll_a, 2: coll_b, 3: coll_c})
        result = repo.get_collections_for_search([1, 2, 3])
        # InMemoryPipelineRepository returns all matching IDs (filtering by is_index
        # is the responsibility of the caller or specific implementation logic)
        assert len(result) == 3
        assert coll_a in result
        assert coll_b in result

    def test_get_collections_for_search_skips_missing_ids(self):
        coll = _collection(name="A", summary="Index A")
        repo = InMemoryPipelineRepository(collections={1: coll})
        result = repo.get_collections_for_search([1, 999])
        assert len(result) == 1
        assert result[0] is coll

    def test_get_collection_index_summaries_returns_formatted_string(self):
        repo = InMemoryPipelineRepository(
            collections={
                1: _collection(name="FAQ", summary="Common questions"),
                2: _collection(name="Docs", summary="Product documentation"),
            }
        )
        result = repo.get_collection_index_summaries([1, 2])
        assert "Collection Index (id=1, name=FAQ): Common questions" in result
        assert "Collection Index (id=2, name=Docs): Product documentation" in result

    def test_get_collection_index_summaries_empty_list_returns_empty_string(self):
        repo = InMemoryPipelineRepository()
        assert repo.get_collection_index_summaries([]) == ""

    def test_get_collection_index_summaries_skips_missing(self):
        repo = InMemoryPipelineRepository(collections={1: _collection(name="FAQ", summary="Common questions")})
        result = repo.get_collection_index_summaries([1, 999])
        assert "FAQ" in result
        assert "999" not in result


# ===========================================================================
# Protocol conformance test
# ===========================================================================


class TestProtocolConformance:
    def test_in_memory_repo_is_instance_of_protocol(self):
        """InMemoryPipelineRepository satisfies the PipelineRepository protocol."""
        from apps.pipelines.repository import PipelineRepository

        repo = InMemoryPipelineRepository()
        assert isinstance(repo, PipelineRepository)


# ===========================================================================
# DB-free node tests using InMemoryPipelineRepository
# ===========================================================================


class TestRenderTemplateNodeWithInMemoryRepo:
    """Test RenderTemplate._process using InMemoryPipelineRepository.

    These tests demonstrate that node logic can be exercised without a
    database by plugging InMemoryPipelineRepository into the NodeContext.
    """

    def test_render_simple_template(self):
        """Basic template rendering works without DB."""
        repo = InMemoryPipelineRepository()
        state = PipelineState(
            messages=["Cycling"],
            outputs={},
            experiment_session=None,
            last_node_input="Cycling",
            node_inputs=["Cycling"],
            temp_state={"my_key": "example_value"},
        )
        template = "input: {{input}}, temp_state.my_key: {{temp_state.my_key}}"
        node = RenderTemplate(name="test", node_id="123", django_node=None, template_string=template)
        context = NodeContext(state, repo=repo)
        output = node._process(state, context)
        assert output["messages"][-1] == "input: Cycling, temp_state.my_key: example_value"

    def test_render_template_with_participant_details(self):
        """Participant details are rendered when session has a participant."""
        repo = InMemoryPipelineRepository()
        session = _mock_session()
        state = PipelineState(
            messages=["hello"],
            outputs={},
            experiment_session=session,
            last_node_input="hello",
            node_inputs=["hello"],
            temp_state={},
            participant_data={"role": "tester"},
        )
        template = "id: {{participant_details.identifier}}, data: {{participant_data.role}}"
        node = RenderTemplate(name="test", node_id="456", django_node=None, template_string=template)
        context = NodeContext(state, repo=repo)
        output = node._process(state, context)
        assert output["messages"][-1] == "id: test-user, data: tester"

    def test_render_template_with_participant_schedules_via_repo(self):
        """RenderTemplate uses repo.get_participant_schedules when available."""
        repo = InMemoryPipelineRepository()
        session = _mock_session()
        state = PipelineState(
            messages=["hi"],
            outputs={},
            experiment_session=session,
            last_node_input="hi",
            node_inputs=["hi"],
            temp_state={},
            participant_data={},
        )
        template = "schedules: {{participant_schedules}}"
        node = RenderTemplate(name="test", node_id="789", django_node=None, template_string=template)
        context = NodeContext(state, repo=repo)
        output = node._process(state, context)
        assert output["messages"][-1] == "schedules: []"
        # Verify the repo recorded the schedule lookup
        assert len(repo.schedule_lookups) == 1
        assert repo.schedule_lookups[0]["experiment_id"] == session.experiment_id

    def test_render_template_with_session_state(self):
        """Session state values are accessible in the template."""
        repo = InMemoryPipelineRepository()
        state = PipelineState(
            messages=["go"],
            outputs={},
            experiment_session=None,
            last_node_input="go",
            node_inputs=["go"],
            temp_state={},
            session_state={"step": 3},
        )
        template = "step: {{session_state.step}}"
        node = RenderTemplate(name="test", node_id="abc", django_node=None, template_string=template)
        context = NodeContext(state, repo=repo)
        output = node._process(state, context)
        assert output["messages"][-1] == "step: 3"


class TestCodeNodeWithInMemoryRepo:
    """Test CodeNode._process using InMemoryPipelineRepository.

    Demonstrates DB-free testing of CodeNode's custom functions that
    interact with the repository (e.g. add_file_attachment).
    """

    def test_code_node_add_file_attachment_uses_repo(self):
        """add_file_attachment creates file and attaches it via InMemoryPipelineRepository."""
        repo = InMemoryPipelineRepository()
        session = _mock_session()
        state = PipelineState(
            messages=["hi"],
            outputs={},
            experiment_session=session,
            last_node_input="hi",
            node_inputs=["hi"],
            temp_state={},
        )
        code = """
def main(input, **kwargs):
    add_file_attachment("report.csv", b"col1,col2\\n1,2", "text/csv")
    return "done"
"""
        node = CodeNode(name="test", node_id="file-node", django_node=None, code=code)
        config = {"configurable": {"repo": repo}}
        node_output = node.process(incoming_nodes=[], outgoing_nodes=[], state=state, config=config)

        # Verify repo recorded file creation
        assert len(repo.files_created) == 1
        assert repo.files_created[0].filename == "report.csv"
        assert repo.files_created[0].content_type == "text/csv"
        assert repo.files_created[0].team_id == session.team_id

        # Verify repo recorded file attachment
        assert len(repo.attached_files) == 1
        assert repo.attached_files[0]["attachment_type"] == "code_interpreter"

        # Verify the output includes the generated file ID in metadata
        generated_files = node_output.update["output_message_metadata"]["generated_files"]  # ty: ignore[not-subscriptable]
        assert repo.files_created[0].id in generated_files

    def test_code_node_basic_processing_without_db(self):
        """A simple CodeNode processes correctly with InMemoryPipelineRepository."""
        repo = InMemoryPipelineRepository()
        state = PipelineState(
            outputs={},
            experiment_session=None,
            last_node_input="World",
            node_inputs=["World"],
        )
        code = "def main(input, **kwargs):\n\treturn f'Hello, {input}!'"
        node = CodeNode(name="test", node_id="code-1", django_node=None, code=code)
        context = NodeContext(state, repo=repo)
        output = node._process(state, context)
        assert output.update["messages"][-1] == "Hello, World!"  # ty: ignore[not-subscriptable]

    def test_code_node_get_participant_data_without_db(self):
        """CodeNode get_participant_data() works with in-memory state."""
        repo = InMemoryPipelineRepository()
        session = _mock_session()
        state = PipelineState(
            messages=["hi"],
            outputs={},
            experiment_session=session,
            last_node_input="hi",
            node_inputs=["hi"],
            temp_state={},
            participant_data={"fun_facts": {"personality": "fun loving", "body_type": "robot"}},
        )
        code = """
def main(input, **kwargs):
    return get_participant_data()["fun_facts"]["body_type"]
"""
        node = CodeNode(name="test", node_id="code-pd", django_node=None, code=code)
        config = {"configurable": {"repo": repo}}
        output = node.process(incoming_nodes=[], outgoing_nodes=[], state=state, config=config)
        assert output.update["messages"][-1] == "robot"  # ty: ignore[not-subscriptable]

    def test_code_node_temp_state_without_db(self):
        """CodeNode set/get_temp_state_key works without DB."""
        repo = InMemoryPipelineRepository()
        state = PipelineState(
            outputs={},
            experiment_session=None,
            last_node_input="hi",
            node_inputs=["hi"],
            temp_state={},
        )
        code = """
def main(input, **kwargs):
    set_temp_state_key("counter", 42)
    return str(get_temp_state_key("counter"))
"""
        node = CodeNode(name="test", node_id="code-ts", django_node=None, code=code)
        context = NodeContext(state, repo=repo)
        output = node._process(state, context)
        assert output.update["messages"][-1] == "42"  # ty: ignore[not-subscriptable]

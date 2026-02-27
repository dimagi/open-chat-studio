import pytest

from apps.pipelines.repository import (
    CollectionFileInfo,
    CollectionIndexSummary,
    InMemoryPipelineRepository,
    ORMRepository,
    PipelineRepository,
    RepositoryLookupError,
)


class TestInMemoryRepository:
    """Tests for InMemoryPipelineRepository — no DB access required."""

    def setup_method(self):
        self.repo = InMemoryPipelineRepository()

    def test_is_pipeline_repository(self):
        assert isinstance(self.repo, PipelineRepository)

    # --- LLM provider / service ---

    def test_get_llm_provider_not_found(self):
        with pytest.raises(RepositoryLookupError, match="LLM provider with id 999"):
            self.repo.get_llm_provider(999)

    def test_get_llm_provider_found(self):
        sentinel = object()
        self.repo.providers[1] = sentinel
        assert self.repo.get_llm_provider(1) is sentinel

    def test_get_llm_service_not_found(self):
        with pytest.raises(RepositoryLookupError, match="LLM service for provider 999"):
            self.repo.get_llm_service(999)

    def test_get_llm_service_found(self):
        sentinel = object()
        self.repo.llm_services[1] = sentinel
        assert self.repo.get_llm_service(1) is sentinel

    # --- Source material ---

    def test_get_source_material_not_found(self):
        with pytest.raises(RepositoryLookupError, match="SourceMaterial with id 999"):
            self.repo.get_source_material(999)

    def test_get_source_material_found(self):
        sentinel = object()
        self.repo.source_materials[1] = sentinel
        assert self.repo.get_source_material(1) is sentinel

    # --- Collections ---

    def test_get_collection_not_found(self):
        with pytest.raises(RepositoryLookupError, match="Collection with id 999"):
            self.repo.get_collection(999)

    def test_get_collection_found(self):
        sentinel = object()
        self.repo.collections[1] = sentinel
        assert self.repo.get_collection(1) is sentinel

    def test_get_collections_for_search_empty(self):
        result = self.repo.get_collections_for_search([1, 2, 3])
        assert result == []

    def test_get_collections_for_search_partial(self):
        c1 = object()
        self.repo.collections[1] = c1
        result = self.repo.get_collections_for_search([1, 2])
        assert result == [c1]

    def test_get_collection_index_summaries(self):
        from types import SimpleNamespace

        c = SimpleNamespace(id=1, name="Test", summary="A summary")
        self.repo.collections[1] = c
        result = self.repo.get_collection_index_summaries([1])
        assert len(result) == 1
        assert result[0] == CollectionIndexSummary(id=1, name="Test", summary="A summary")

    def test_get_collection_file_info_not_found(self):
        with pytest.raises(RepositoryLookupError, match="Collection with id 999"):
            self.repo.get_collection_file_info(999)

    def test_get_collection_file_info_found(self):
        self.repo.collection_files[1] = [
            CollectionFileInfo(id=10, summary="sum", content_type="text/plain"),
        ]
        result = self.repo.get_collection_file_info(1)
        assert len(result) == 1
        assert result[0].id == 10

    # --- Chat history ---

    def test_get_pipeline_chat_history_creates_on_first_call(self):
        history = self.repo.get_pipeline_chat_history(session=None, history_type="node", name="test-node")
        assert history is not None
        assert history.type == "node"
        assert history.name == "test-node"

    def test_get_pipeline_chat_history_returns_same_on_second_call(self):
        h1 = self.repo.get_pipeline_chat_history(session=None, history_type="node", name="test-node")
        h2 = self.repo.get_pipeline_chat_history(session=None, history_type="node", name="test-node")
        assert h1 is h2

    def test_save_pipeline_chat_message(self):
        msg = self.repo.save_pipeline_chat_message(
            history=None, human_message="Hello", ai_message="Hi there", node_id="node-1"
        )
        assert msg.human_message == "Hello"
        assert msg.ai_message == "Hi there"
        assert len(self.repo.history_messages) == 1

    def test_get_session_messages_returns_preloaded(self):
        from langchain_core.messages import HumanMessage

        self.repo.session_messages = [HumanMessage(content="test")]
        result = self.repo.get_session_messages(session=None, history_mode="summarize")
        assert len(result) == 1
        assert result[0].content == "test"

    def test_save_compression_checkpoint(self):
        self.repo.save_compression_checkpoint(
            checkpoint_message_id=1, history_type="global", compression_marker="marker", history_mode="summarize"
        )
        assert len(self.repo.compression_checkpoints) == 1

    # --- Files ---

    def test_create_file(self):
        from io import BytesIO

        file = self.repo.create_file(
            filename="test.txt", file_obj=BytesIO(b"content"), team_id=1, content_type="text/plain", purpose="test"
        )
        assert file.name == "test.txt"
        assert len(self.repo.files_created) == 1

    def test_attach_files_to_chat(self):
        self.repo.attach_files_to_chat(session="fake_session", attachment_type="code_interpreter", files=["file1"])
        assert len(self.repo.attached_files) == 1
        assert self.repo.attached_files[0]["type"] == "code_interpreter"

    # --- Participant ---

    def test_get_participant_global_data(self):
        self.repo.participant_global_data = {"key": "value"}
        result = self.repo.get_participant_global_data(participant=None)
        assert result == {"key": "value"}

    def test_get_participant_schedules(self):
        self.repo.participant_schedules = [{"id": 1}]
        result = self.repo.get_participant_schedules(participant=None, experiment_id=1)
        assert result == [{"id": 1}]

    # --- Assistants ---

    def test_get_assistant_not_found(self):
        with pytest.raises(RepositoryLookupError, match="Assistant with id 999"):
            self.repo.get_assistant(999)

    def test_get_assistant_found(self):
        sentinel = object()
        self.repo.assistants[1] = sentinel
        assert self.repo.get_assistant(1) is sentinel


@pytest.mark.django_db()
class TestORMRepository:
    """Tests for ORMRepository — requires DB."""

    def setup_method(self):
        self.repo = ORMRepository()

    def test_is_pipeline_repository(self):
        assert isinstance(self.repo, PipelineRepository)

    def test_get_llm_provider_not_found(self):
        with pytest.raises(RepositoryLookupError, match="LLM provider with id 999999"):
            self.repo.get_llm_provider(999999)

    def test_get_source_material_not_found(self):
        with pytest.raises(RepositoryLookupError, match="SourceMaterial with id 999999"):
            self.repo.get_source_material(999999)

    def test_get_collection_not_found(self):
        with pytest.raises(RepositoryLookupError, match="Collection with id 999999"):
            self.repo.get_collection(999999)

    def test_get_assistant_not_found(self):
        with pytest.raises(RepositoryLookupError, match="Assistant with id 999999"):
            self.repo.get_assistant(999999)

    def test_get_source_material_found(self):
        from apps.utils.factories.experiment import SourceMaterialFactory

        sm = SourceMaterialFactory()
        result = self.repo.get_source_material(sm.id)
        assert result.id == sm.id

    def test_get_collection_found(self):
        from apps.utils.factories.documents import CollectionFactory

        col = CollectionFactory()
        result = self.repo.get_collection(col.id)
        assert result.id == col.id

    def test_get_collections_for_search_empty(self):
        result = self.repo.get_collections_for_search([999999])
        assert result == []

    def test_get_collection_index_summaries(self):
        from apps.utils.factories.documents import CollectionFactory

        col = CollectionFactory(name="My Collection", summary="A summary")
        result = self.repo.get_collection_index_summaries([col.id])
        assert len(result) == 1
        assert result[0] == CollectionIndexSummary(id=col.id, name="My Collection", summary="A summary")

    def test_get_pipeline_chat_history_creates(self):
        from apps.utils.factories.experiment import ExperimentSessionFactory

        session = ExperimentSessionFactory()
        history = self.repo.get_pipeline_chat_history(session, "node", "test-node")
        assert history.type == "node"
        assert history.name == "test-node"

    def test_save_pipeline_chat_message(self):
        from apps.utils.factories.experiment import ExperimentSessionFactory

        session = ExperimentSessionFactory()
        history = self.repo.get_pipeline_chat_history(session, "node", "test-node")
        msg = self.repo.save_pipeline_chat_message(history, "Hello", "Hi", "node-1")
        assert msg.human_message == "Hello"
        assert msg.ai_message == "Hi"
        assert msg.node_id == "node-1"

    def test_get_session_messages(self):
        from apps.utils.factories.experiment import ExperimentSessionFactory

        session = ExperimentSessionFactory()
        # With no messages, should return empty list
        result = self.repo.get_session_messages(session, "summarize")
        assert result == []

    def test_attach_files_to_chat(self):
        from apps.utils.factories.experiment import ExperimentSessionFactory

        session = ExperimentSessionFactory()
        # Should not raise — attaching empty list
        self.repo.attach_files_to_chat(session, "code_interpreter", [])

    def test_get_participant_global_data(self):
        from apps.utils.factories.experiment import ExperimentSessionFactory

        session = ExperimentSessionFactory()
        result = self.repo.get_participant_global_data(session.participant)
        assert isinstance(result, dict)

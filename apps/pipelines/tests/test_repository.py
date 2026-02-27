from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.nodes.nodes import Passthrough
from apps.pipelines.repository import (
    CollectionFileInfo,
    CollectionIndexSummary,
    InMemoryPipelineRepository,
    ORMRepository,
    PipelineRepository,
    RepositoryLookupError,
)
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.experiment import ExperimentSessionFactory


def _make_in_memory():
    return InMemoryPipelineRepository()


def _make_orm():
    return ORMRepository()


@pytest.mark.django_db()
@pytest.mark.parametrize("factory", [_make_in_memory, _make_orm], ids=["in_memory", "orm"])
class TestSharedRepositoryContract:
    """Parametrized tests that verify both implementations satisfy the PipelineRepository contract."""

    def test_is_pipeline_repository(self, factory):
        repo = factory()
        assert isinstance(repo, PipelineRepository)

    def test_get_llm_provider_not_found(self, factory):
        repo = factory()
        with pytest.raises(RepositoryLookupError, match="LLM provider"):
            repo.get_llm_provider(999999)

    def test_get_source_material_not_found(self, factory):
        repo = factory()
        with pytest.raises(RepositoryLookupError, match="SourceMaterial"):
            repo.get_source_material(999999)

    def test_get_collection_not_found(self, factory):
        repo = factory()
        with pytest.raises(RepositoryLookupError, match="Collection"):
            repo.get_collection(999999)

    def test_get_assistant_not_found(self, factory):
        repo = factory()
        with pytest.raises(RepositoryLookupError, match="Assistant"):
            repo.get_assistant(999999)

    def test_get_collection_file_info_not_found(self, factory):
        repo = factory()
        with pytest.raises(RepositoryLookupError, match="Collection"):
            repo.get_collection_file_info(999999)

    def test_get_collections_for_search_missing_ids(self, factory):
        repo = factory()
        result = repo.get_collections_for_search([999999])
        assert result == []

    def test_get_collection_index_summaries_missing_ids(self, factory):
        repo = factory()
        result = repo.get_collection_index_summaries([999999])
        assert result == []


class TestInMemoryRepository:
    """Tests for InMemoryPipelineRepository — no DB access required."""

    def setup_method(self):
        self.repo = InMemoryPipelineRepository()

    def test_get_llm_service_not_found(self):
        with pytest.raises(RepositoryLookupError, match="LLM service for provider 999"):
            self.repo.get_llm_service(999)

    def test_get_collections_for_search_partial(self):
        c1 = object()
        self.repo.collections[1] = c1
        result = self.repo.get_collections_for_search([1, 2])
        assert result == [c1]

    def test_get_collection_index_summaries(self):
        c = SimpleNamespace(id=1, name="Test", summary="A summary")
        self.repo.collections[1] = c
        result = self.repo.get_collection_index_summaries([1])
        assert len(result) == 1
        assert result[0] == CollectionIndexSummary(id=1, name="Test", summary="A summary")

    def test_get_collection_file_info_found(self):
        self.repo.collection_files[1] = [
            CollectionFileInfo(id=10, summary="sum", content_type="text/plain"),
        ]
        result = self.repo.get_collection_file_info(1)
        assert len(result) == 1
        assert result[0].id == 10

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

    def test_create_file(self):
        file = self.repo.create_file(
            filename="test.txt", file_obj=BytesIO(b"content"), team_id=1, content_type="text/plain", purpose="test"
        )
        assert file.name == "test.txt"
        assert len(self.repo.files_created) == 1


class TestInjectionWiring:
    """Verify that PipelineNode.process() extracts repo from LangGraph config."""

    def test_node_receives_repo_from_config(self):
        node = Passthrough(node_id="test-node", name="test", django_node=None)
        repo = ORMRepository()
        config = {"configurable": {"repo": repo}}
        state = PipelineState(messages=["hello"])

        # Mock _prepare_state and _process to isolate the wiring test
        with (
            patch.object(node, "_prepare_state", return_value=state),
            patch.object(node, "_process", return_value=None),
        ):
            node.process(incoming_nodes=[], outgoing_nodes=[], state=state, config=config)

        assert node._repo is repo
        assert isinstance(node._repo, ORMRepository)


@pytest.mark.django_db()
class TestORMRepository:
    """Tests for ORMRepository — requires DB."""

    def setup_method(self):
        self.repo = ORMRepository()

    def test_get_collection_index_summaries(self):
        col = CollectionFactory(name="My Collection", summary="A summary")
        result = self.repo.get_collection_index_summaries([col.id])
        assert len(result) == 1
        assert result[0] == CollectionIndexSummary(id=col.id, name="My Collection", summary="A summary")

    def test_get_pipeline_chat_history_creates(self):
        session = ExperimentSessionFactory()
        history = self.repo.get_pipeline_chat_history(session, "node", "test-node")
        assert history.type == "node"
        assert history.name == "test-node"

    def test_save_pipeline_chat_message(self):
        session = ExperimentSessionFactory()
        history = self.repo.get_pipeline_chat_history(session, "node", "test-node")
        msg = self.repo.save_pipeline_chat_message(history, "Hello", "Hi", "node-1")
        assert msg.human_message == "Hello"
        assert msg.ai_message == "Hi"
        assert msg.node_id == "node-1"

"""Regression tests for #3433 - chunks from FAILED files must not surface in retrieval.

Two distinct scenarios are covered, and they guard different halves of the fix:

* A file that fails partway through indexing now. The write-side cleanup removes the
  chunks it managed to persist, so nothing reaches retrieval in the first place.
* A collection corrupted *before* this fix shipped, where orphaned chunks are already
  sitting in the database under a FAILED file. Write-side cleanup cannot reach those, so
  the read filter and the discard step in the retry view are all that protect against
  them. These are seeded directly rather than produced through `add_files`, because
  `add_files` no longer leaves orphans behind and would mask the very code under test.
"""

from unittest import mock

import pytest
from django.conf import settings
from django.urls import reverse

from apps.chat.agent.tools import _perform_collection_search
from apps.documents.models import CollectionFile, FileStatus
from apps.files.models import FileChunkEmbedding
from apps.service_providers.llm_service.index_managers import LocalIndexManager
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.files import FileFactory
from apps.utils.factories.team import TeamWithUsersFactory

CHUNKS = ["alpha chunk", "beta chunk", "gamma chunk"]


class LocalIndexManagerMock(LocalIndexManager):
    """A local index manager that chunks and embeds without calling a provider."""

    def chunk_file(self, text, chunk_size=None, chunk_overlap=None):
        """Return a fixed set of chunks, so tests control exactly how many embeddings are attempted."""
        return CHUNKS

    def get_embedding_vector(self, text, *, input_type):  # ty: ignore[invalid-method-override]
        """Return a constant vector. Distance is irrelevant here; presence in the results is what matters."""
        return [0.1] * settings.EMBEDDING_VECTOR_SIZE


@pytest.fixture()
def collection(db):
    """A local (not remote) indexed collection whose team has a member, for the view tests."""
    return CollectionFactory.create(team=TeamWithUsersFactory.create(), is_index=True, is_remote_index=False)


@pytest.fixture()
def index_manager():
    """Patch the provider lookup so the collection resolves to the mock manager."""
    with mock.patch("apps.service_providers.models.LlmProvider.get_local_index_manager") as get_manager:
        manager = LocalIndexManagerMock(api_key="api-123", embedding_model_name="embedding-model")
        get_manager.return_value = manager
        yield manager


def _index_file_failing_on_last_chunk(collection, index_manager):
    """Index a file where embedding succeeds for chunks 1-2 and fails on chunk 3."""
    file = FileFactory.create(team=collection.team)
    collection.files.add(file)
    collection_file = CollectionFile.objects.get(collection=collection, file=file)
    collection_file.status = FileStatus.PENDING
    collection_file.save()

    good_vector = [0.1] * settings.EMBEDDING_VECTOR_SIZE
    side_effect = [good_vector, good_vector, Exception("provider blew up on chunk 3")]

    with mock.patch.object(index_manager, "get_embedding_vector", side_effect=side_effect):
        index_manager.add_files(CollectionFile.objects.filter(id=collection_file.id).iterator(1))

    collection_file.refresh_from_db()
    return file, collection_file


def _seed_orphans_from_a_pre_fix_failure(collection, texts, status=FileStatus.FAILED):
    """Put chunks in the database under a file with the given status, bypassing add_files.

    This is what a collection corrupted by the old code looks like: the file is marked
    FAILED but the chunks written before the provider gave up are still there. Seeding
    directly matters, because add_files now cleans up after itself and would leave nothing
    for the read filter or the delete-before-indexing step to act on.
    """
    file = FileFactory.create(team=collection.team)
    collection.files.add(file, through_defaults={"status": status})
    collection_file = CollectionFile.objects.get(collection=collection, file=file)

    for chunk_number, text in enumerate(texts, start=1):
        FileChunkEmbedding.objects.create(
            team_id=collection.team_id,
            file=file,
            collection=collection,
            chunk_number=chunk_number,
            text=text,
            embedding=[0.1] * settings.EMBEDDING_VECTOR_SIZE,
            page_number=0,
        )

    return file, collection_file


@pytest.mark.django_db()
class TestFailedFileRetrieval:
    """A file failing to index now, where the write-side cleanup should leave nothing behind."""

    def test_partial_failure_cleans_up_persisted_chunks(self, collection, index_manager):
        """A file that fails partway must not leave half its chunks in the database."""
        file, collection_file = _index_file_failing_on_last_chunk(collection, index_manager)

        assert collection_file.status == FileStatus.FAILED
        assert FileChunkEmbedding.objects.filter(file=file, collection=collection).count() == 0

    def test_index_manager_query_excludes_failed_file_chunks(self, collection, index_manager):
        """The Query Collection preview must not return chunks from a FAILED file."""
        _index_file_failing_on_last_chunk(collection, index_manager)

        results = index_manager.query(index_id=collection.id, query="anything", top_k=5)

        assert list(results) == []

    def test_chat_search_excludes_failed_file_chunks(self, collection, index_manager):
        """The path a chatbot retrieves through must not return chunks from a FAILED file."""
        _index_file_failing_on_last_chunk(collection, index_manager)

        output = _perform_collection_search(collection, query="anything", max_results=5)

        assert "alpha chunk" not in output
        assert "beta chunk" not in output

    def test_retry_after_failure_does_not_duplicate_chunks(self, collection, index_manager):
        """Re-indexing a previously failed file must replace its chunks, not add to them."""
        file, collection_file = _index_file_failing_on_last_chunk(collection, index_manager)

        # Simulate documents.views.retry_failed_uploads: FAILED -> PENDING, then re-index.
        collection_file.status = FileStatus.PENDING
        collection_file.save()
        index_manager.add_files(CollectionFile.objects.filter(id=collection_file.id).iterator(1))

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.COMPLETED

        texts = sorted(e.text for e in FileChunkEmbedding.objects.filter(file=file, collection=collection))
        assert texts == sorted(CHUNKS)

    def test_publishing_does_not_copy_failed_file_chunks(self, collection, index_manager):
        """A published version must not inherit chunks from a file that failed to index."""
        _index_file_failing_on_last_chunk(collection, index_manager)

        new_version = collection.create_new_version()

        assert FileChunkEmbedding.objects.filter(collection=new_version).count() == 0


@pytest.mark.django_db()
class TestOrphansFromBeforeTheFix:
    """Collections already carrying orphaned chunks when this fix ships.

    Write-side cleanup cannot help here, so these are the only tests holding the read
    filter and the retry-time discard honest.
    """

    def test_retrieval_hides_orphans_left_by_the_old_code(self, collection, index_manager):
        """Neither retrieval path may return orphans that predate this fix."""
        _seed_orphans_from_a_pre_fix_failure(collection, ["alpha chunk", "beta chunk"])

        results = index_manager.query(index_id=collection.id, query="anything", top_k=5)
        output = _perform_collection_search(collection, query="anything", max_results=5)

        assert list(results) == []
        assert "alpha chunk" not in output
        assert "beta chunk" not in output

    @pytest.mark.parametrize(
        ("status", "expected_visible"),
        [
            pytest.param(FileStatus.COMPLETED, True, id="completed-is-served"),
            pytest.param("", True, id="blank-status-from-versioning-is-served"),
            pytest.param(FileStatus.FAILED, False, id="failed-is-hidden"),
            pytest.param(FileStatus.IN_PROGRESS, False, id="killed-mid-index-is-hidden"),
            pytest.param(FileStatus.PENDING, False, id="queued-for-retry-is-hidden"),
        ],
    )
    def test_retrieval_visibility_by_file_status(self, collection, index_manager, status, expected_visible):
        """Only a file that has finished indexing may be retrieved from.

        Blank must stay visible: `create_new_version` leaves it blank, so hiding it would
        black out retrieval for every published collection. PENDING and IN_PROGRESS must be
        hidden because a file queued for retry, or one whose worker was killed mid-index,
        still has stale chunks sitting under it.
        """
        _seed_orphans_from_a_pre_fix_failure(collection, ["alpha chunk"], status=status)

        results = index_manager.query(index_id=collection.id, query="anything", top_k=5)

        assert bool(list(results)) is expected_visible

    def test_publishing_a_pre_fix_collection_drops_the_orphans(self, collection, index_manager):
        """Publishing must not launder orphans past the filter.

        A version's CollectionFile rows carry a blank status, so any orphan copied into a
        version becomes permanently visible. They must not be copied in the first place.
        """
        _seed_orphans_from_a_pre_fix_failure(collection, ["alpha chunk", "beta chunk"])

        new_version = collection.create_new_version()

        assert FileChunkEmbedding.objects.filter(collection=new_version).count() == 0

    def test_retrying_discards_orphans_before_reindexing(self, collection, index_manager, client):
        """Retrying a failed upload must clear its stale chunks, so the retry cannot stack on them.

        The discard happens in the retry view rather than in `add_files`, because
        `FileChunkEmbedding.working_version` cascades: deleting a working chunk destroys the
        copies held by published versions. Only failed files are discarded here, so no
        successfully indexed content is ever at risk.
        """
        file, collection_file = _seed_orphans_from_a_pre_fix_failure(collection, ["alpha chunk", "beta chunk"])
        client.force_login(collection.team.members.first())

        url = reverse("documents:retry_failed_uploads", args=[collection.team.slug, collection.id])
        with mock.patch("apps.documents.tasks.index_collection_files_task.delay") as index_task:
            response = client.post(url)

        assert response.status_code == 302
        index_task.assert_called_once_with([collection_file.id])

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.PENDING
        assert FileChunkEmbedding.objects.filter(file=file, collection=collection).count() == 0

    def test_retrying_leaves_published_version_chunks_alone(self, collection, index_manager, client):
        """Discarding a failed file's chunks must not reach into a published version.

        `working_version` is a CASCADE self-FK, so this guards against the discard taking a
        published collection's content with it.
        """
        good_file = FileFactory.create(team=collection.team)
        collection.files.add(good_file, through_defaults={"status": FileStatus.COMPLETED})
        FileChunkEmbedding.objects.create(
            team_id=collection.team_id,
            file=good_file,
            collection=collection,
            chunk_number=1,
            text="healthy chunk",
            embedding=[0.1] * settings.EMBEDDING_VECTOR_SIZE,
            page_number=0,
        )
        _seed_orphans_from_a_pre_fix_failure(collection, ["alpha chunk"])
        new_version = collection.create_new_version()
        client.force_login(collection.team.members.first())

        url = reverse("documents:retry_failed_uploads", args=[collection.team.slug, collection.id])
        with mock.patch("apps.documents.tasks.index_collection_files_task.delay"):
            client.post(url)

        published = FileChunkEmbedding.objects.filter(collection=new_version)
        assert [chunk.text for chunk in published] == ["healthy chunk"]

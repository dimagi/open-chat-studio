from unittest import mock

import pytest
from django.conf import settings

from apps.documents.models import CollectionFile, FileStatus
from apps.files.models import FileChunkEmbedding
from apps.service_providers.llm_service.index_managers import LocalIndexManager
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.files import FileFactory


class LocalIndexManagerMock(LocalIndexManager):
    def chunk_file(self, text, chunk_size=None, chunk_overlap=None):
        return ["test", "content"]

    def get_embedding_vector(self, text, *, input_type):  # ty: ignore[invalid-method-override]
        return [0.1] * settings.EMBEDDING_VECTOR_SIZE


@pytest.fixture()
def local_index_instance(db):
    return CollectionFactory.create(is_index=True, is_remote_index=False)


FAQ_CSV = (
    b"question,answer,language\n"
    b"When is the clinic open?,08:00 to 16:00,en\n"
    b"Ivulwa nini ikliniki?,08:00 ukuya ku-16:00,xh\n"
    b"Where is the clinic?,Site B,en\n"
)


@pytest.mark.django_db()
class TestLocalIndexManagerRowImport:
    @pytest.fixture()
    def index_manager(self):
        with mock.patch("apps.service_providers.models.LlmProvider.get_local_index_manager") as get_local_index_manager:
            manager = LocalIndexManagerMock(api_key="api-123", embedding_model_name="embedding-model")
            get_local_index_manager.return_value = manager
            yield manager

    @pytest.fixture()
    def collection_file(self, local_index_instance):
        file = FileFactory.create(team=local_index_instance.team, name="faq.csv", file__data=FAQ_CSV)
        return CollectionFile.objects.create(
            collection=local_index_instance,
            file=file,
            status=FileStatus.PENDING,
            metadata={"row_import": {"metadata_columns": ["language"]}},
        )

    def test_one_chunk_per_row_with_metadata(self, collection_file, index_manager):
        index_manager.add_files(CollectionFile.objects.filter(id=collection_file.id).iterator(1))

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.COMPLETED
        assert collection_file.failure_reason == ""
        chunks = list(FileChunkEmbedding.objects.filter(file=collection_file.file).order_by("chunk_number"))
        assert [chunk.page_number for chunk in chunks] == [1, 2, 3]
        assert chunks[0].text == "faq.csv\nquestion: When is the clinic open?\nanswer: 08:00 to 16:00\nlanguage: en"
        assert chunks[0].metadata == {"language": "en"}
        assert chunks[1].metadata == {"language": "xh"}
        assert len(chunks[0].content_hash) == 64
        assert chunks[0].context == ""
        assert all(chunk.search_vector is not None for chunk in chunks)

    def test_rows_are_embedded_in_batches(self, collection_file, index_manager):
        with mock.patch.object(
            index_manager, "get_embedding_vectors", wraps=index_manager.get_embedding_vectors
        ) as spy:
            index_manager.add_files(CollectionFile.objects.filter(id=collection_file.id).iterator(1))

        assert spy.call_count == 1
        assert len(spy.call_args.args[0]) == 3

    def test_contextualizer_is_not_used_for_rows(self, collection_file, index_manager):
        index_manager._contextualizer = mock.Mock()

        index_manager.add_files(CollectionFile.objects.filter(id=collection_file.id).iterator(1))

        index_manager._contextualizer.get_context.assert_not_called()

    def test_a_failing_row_is_recorded_and_the_rest_are_indexed(self, collection_file, index_manager):
        good = [0.1] * settings.EMBEDDING_VECTOR_SIZE

        def embed_one(content, *, input_type):
            if "Site B" in content:
                raise ValueError("Error code: 400 - input too long")
            return good

        with (
            mock.patch.object(index_manager, "get_embedding_vectors", side_effect=RuntimeError("batch failed")),
            mock.patch.object(index_manager, "get_embedding_vector", side_effect=embed_one),
        ):
            index_manager.add_files(CollectionFile.objects.filter(id=collection_file.id).iterator(1))

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.COMPLETED
        assert collection_file.failure_reason == (
            "1 of 3 rows failed to index. Row 3: ValueError: Error code: 400 - input too long"
        )
        assert FileChunkEmbedding.objects.filter(file=collection_file.file).count() == 2

    def test_a_batch_whose_every_row_fails_marks_the_file_failed_with_the_batch_error(
        self, collection_file, index_manager
    ):
        with (
            mock.patch.object(
                index_manager, "get_embedding_vectors", side_effect=RuntimeError("Error code: 429 - rate limited")
            ),
            mock.patch.object(index_manager, "get_embedding_vector", side_effect=ValueError("no key")) as row_spy,
        ):
            index_manager.add_files(CollectionFile.objects.filter(id=collection_file.id).iterator(1))

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.FAILED
        assert collection_file.failure_reason == "RuntimeError: Error code: 429 - rate limited"
        assert row_spy.call_count == 3
        assert not FileChunkEmbedding.objects.filter(file=collection_file.file).exists()

    def test_unparseable_sheet_marks_the_file_failed(self, local_index_instance, index_manager):
        file = FileFactory.create(team=local_index_instance.team, name="bad.csv", file__data=b"a,b\n1,2,3\n")
        collection_file = CollectionFile.objects.create(
            collection=local_index_instance,
            file=file,
            status=FileStatus.PENDING,
            metadata={"row_import": {"metadata_columns": []}},
        )

        index_manager.add_files(CollectionFile.objects.filter(id=collection_file.id).iterator(1))

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.FAILED
        assert collection_file.failure_reason == "FileReadException: Row 1 has 3 values but the header has 2"

    @pytest.fixture()
    def large_collection_file(self, local_index_instance):
        data = b"a,b\n" + b"".join(f"v{i},w{i}\n".encode() for i in range(150))
        file = FileFactory.create(team=local_index_instance.team, name="large.csv", file__data=data)
        return CollectionFile.objects.create(
            collection=local_index_instance,
            file=file,
            status=FileStatus.PENDING,
            metadata={"row_import": {"metadata_columns": []}},
        )

    def test_rows_beyond_the_batch_size_are_sent_in_further_batches(self, large_collection_file, index_manager):
        with mock.patch.object(
            index_manager, "get_embedding_vectors", wraps=index_manager.get_embedding_vectors
        ) as spy:
            index_manager.add_files(CollectionFile.objects.filter(id=large_collection_file.id).iterator(1))

        large_collection_file.refresh_from_db()
        assert large_collection_file.status == FileStatus.COMPLETED
        assert FileChunkEmbedding.objects.filter(file=large_collection_file.file).count() == 150
        assert [len(call.args[0]) for call in spy.call_args_list] == [100, 50]

    def test_a_failing_batch_falls_back_row_by_row_and_the_next_batch_is_unaffected(
        self, large_collection_file, index_manager
    ):
        vector = [0.1] * settings.EMBEDDING_VECTOR_SIZE
        batch_calls = []

        def embed_documents(contents):
            batch_calls.append(len(contents))
            if len(batch_calls) == 1:
                raise RuntimeError("batch failed")
            return [vector] * len(contents)

        with (
            mock.patch.object(index_manager, "get_embedding_vectors", side_effect=embed_documents),
            mock.patch.object(
                index_manager, "get_embedding_vector", wraps=index_manager.get_embedding_vector
            ) as row_spy,
        ):
            index_manager.add_files(CollectionFile.objects.filter(id=large_collection_file.id).iterator(1))

        large_collection_file.refresh_from_db()
        assert large_collection_file.status == FileStatus.COMPLETED
        assert large_collection_file.failure_reason == ""
        assert FileChunkEmbedding.objects.filter(file=large_collection_file.file).count() == 150
        assert row_spy.call_count == 100

    def test_text_files_still_take_the_chunking_path(self, local_index_instance, index_manager):
        file = FileFactory.create(team=local_index_instance.team, file__data=b"test content")
        local_index_instance.files.add(file)
        collection_file = CollectionFile.objects.get(collection=local_index_instance, file=file)

        index_manager.add_files(CollectionFile.objects.filter(id=collection_file.id).iterator(1))

        chunks = FileChunkEmbedding.objects.filter(file=file)
        assert chunks.count() == 2
        assert all(chunk.metadata is None for chunk in chunks)

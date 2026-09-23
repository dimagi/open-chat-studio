import pytest
from django.conf import settings

from apps.files.models import FileChunkEmbedding
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.files import FileChunkEmbeddingFactory, FileFactory


@pytest.mark.django_db()
def test_versioning_copies_row_metadata_and_content_hash(local_index_manager_mock):
    collection = CollectionFactory.create(is_index=True, is_remote_index=False)
    file = FileFactory.create(team=collection.team)
    collection.files.add(file)
    FileChunkEmbedding.objects.create(
        team_id=collection.team_id,
        file=file,
        collection=collection,
        chunk_number=1,
        page_number=1,
        text="question: When is the clinic open?\nanswer: 08:00 to 16:00",
        embedding=[0.1] * settings.EMBEDDING_VECTOR_SIZE,
        metadata={"language": "en", "district": "Khayelitsha"},
        content_hash="a" * 64,
    )

    new_version = collection.create_new_version()

    copied = FileChunkEmbedding.objects.get(collection=new_version)
    assert copied.metadata == {"language": "en", "district": "Khayelitsha"}
    assert copied.content_hash == "a" * 64


@pytest.mark.django_db()
def test_text_chunks_have_no_row_metadata():
    chunk = FileChunkEmbeddingFactory.create()

    assert chunk.metadata is None
    assert chunk.content_hash is None

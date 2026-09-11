"""Shared scaffolding for the collection retrieval tests.

`test_retrieval.py` covers dense search, lexical search and their fusion; `test_reranking.py`
covers the rerank stage. Both need the same fixture collection and one-hot embeddings, so those
live here rather than being imported from one test module into the other.
"""

from types import SimpleNamespace
from unittest import mock

from django.conf import settings
from waffle.testutils import override_flag

from apps.documents.models import CollectionFile, FileStatus
from apps.documents.rerankers import RerankedDocument, Reranker
from apps.documents.retrieval import search_collection
from apps.service_providers.llm_service.index_managers import LocalIndexManager
from apps.service_providers.models import LlmProviderTypes
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.files import FileChunkEmbeddingFactory, FileFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory

HYBRID_FLAG = "flag_hybrid_search"
RERANK_FLAG = "flag_reranking"


def voyage_response(*pairs: tuple[int, float]):
    """A stand-in for `voyageai.object.reranking.RerankingObject`.

    Only `results[].index` and `results[].relevance_score` are read, so the double stays at that
    shape rather than reproducing the SDK's class.
    """
    return SimpleNamespace(results=[SimpleNamespace(index=index, relevance_score=score) for index, score in pairs])


def make_indexed_collection(**kwargs):
    """A collection whose chunks all belong to a cleanly indexed file."""
    collection = CollectionFactory.create(is_index=True, is_remote_index=False, **kwargs)
    file = FileFactory.create(team=collection.team)
    CollectionFile.objects.create(collection=collection, file=file, status=FileStatus.COMPLETED)
    return collection, file


def add_chunk(collection, file, text, embedding, context=""):
    chunk = FileChunkEmbeddingFactory.create(
        team=collection.team,
        collection=collection,
        file=file,
        text=text,
        context=context,
        embedding=embedding,
    )
    # Build the lexical vector through the same helper the indexing pipeline uses, so these tests
    # exercise the production path rather than a reimplementation of it.
    LocalIndexManager._build_search_vectors([chunk], collection)
    chunk.refresh_from_db()
    return chunk


def unit_vector(index: int) -> list[float]:
    """A one-hot embedding, so cosine similarity between distinct vectors is controllable."""
    vector = [0.0] * settings.EMBEDDING_VECTOR_SIZE
    vector[index] = 1.0
    return vector


class StubReranker(Reranker):
    """A reranker whose answer is scripted, so the stage's own behaviour is what is under test.

    `error` makes the call fail the way a provider outage does, which is the path the stage's
    fallback exists for.
    """

    def __init__(self, ranking: list[RerankedDocument] | None = None, *, error: Exception | None = None):
        self._ranking = ranking or []
        self._error = error
        self.calls: list[tuple[str, list[str], int]] = []

    def rerank(self, query, documents, *, limit):
        self.calls.append((query, list(documents), limit))
        if self._error:
            raise self._error
        return self._ranking


def search_with_reranker(collection, query, reranker, *, top_k=5, context=None, hybrid=False):
    """Run `search_collection` with `reranker` as the collection's reranker.

    `Collection.get_reranker` is stubbed rather than the provider SDK so these tests exercise the
    rerank stage, not the Voyage adapter (which has its own tests). The gating that decides
    whether a reranker is built at all is tested separately, against the real method.
    """
    with mock.patch.object(type(collection), "get_query_vector", return_value=unit_vector(0)):
        with mock.patch.object(type(collection), "get_reranker", return_value=reranker):
            with override_flag(HYBRID_FLAG, active=hybrid):
                return search_collection(collection, query, top_k=top_k, context=context)


def rerankable_collection(**kwargs):
    """A collection configured so that `get_reranker` has everything it needs."""
    collection, file = make_indexed_collection(enable_reranking=True, **kwargs)
    collection.reranker_provider = LlmProviderFactory.create(
        team=collection.team,
        type=str(LlmProviderTypes.voyage),
        config={"voyage_api_key": "test-voyage-key"},
    )
    collection.save()
    return collection, file

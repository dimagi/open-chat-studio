"""Rerankers: rescore retrieval candidates by looking at the query and the chunk together.

Neither half of hybrid search ever sees a query-chunk pair. A chunk's embedding is computed at
indexing time, before any query exists, and `ts_rank_cd` only counts term overlap. A reranker
scores the pair directly, which is why it can reorder candidates that fusion ranked purely on
how each half happened to retrieve them.

Only a hosted reranker is implemented, backed by Voyage AI. A local cross-encoder and Cohere are
both left as follow-ups: each would add a third-party dependency, where Voyage adds none.

There is no no-op implementation. A collection with reranking off returns None from
`Collection.get_reranker()` and `apps.documents.retrieval` skips the stage outright, which is
both cheaper than scoring through an identity function and leaves the `distance` and
`fused_score` annotations that the collection query preview renders in place.
"""

from abc import ABCMeta, abstractmethod
from collections.abc import Sequence
from typing import NamedTuple

# Voyage's client does not retry at all by default. One retry absorbs a transient rate limit
# without turning a rerank into an unbounded wait, and the timeout bounds each attempt, so the
# worst case a chat request can spend in this stage is two timeouts before it falls back to the
# un-reranked ranking.
VOYAGE_TIMEOUT_SECONDS = 10.0
VOYAGE_MAX_RETRIES = 1


class RerankedDocument(NamedTuple):
    """One scored document, identified by its position in the list handed to `rerank`."""

    index: int
    score: float


class Reranker(metaclass=ABCMeta):
    """Scores (query, document) pairs and returns the best documents, best first."""

    @abstractmethod
    def rerank(self, query: str, documents: Sequence[str], *, limit: int) -> list[RerankedDocument]:
        """Score every document against `query` and return at most `limit`, best first.

        Implementations raise on failure rather than degrading. The fallback policy -- keep the
        order retrieval already produced -- belongs to the single caller in
        `apps.documents.retrieval`, so it is stated once instead of in every implementation, and
        an empty return stays distinguishable from a call that failed.
        """


class VoyageReranker(Reranker):
    """Voyage AI's hosted rerank endpoint.

    Voyage rather than another hosted reranker because OCS already ships the `voyageai` client
    (as a dependency of `langchain-voyageai`, used for Voyage embeddings) and already models
    Voyage credentials as an `LlmProvider`. Reranking through it therefore adds no third-party
    dependency and no new provider type.
    """

    def __init__(self, api_key: str, model: str, *, timeout: float = VOYAGE_TIMEOUT_SECONDS):
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    def rerank(self, query: str, documents: Sequence[str], *, limit: int) -> list[RerankedDocument]:
        documents = list(documents)
        if not documents or limit <= 0:
            # Voyage rejects an empty document list, and there is nothing for the caller to
            # reorder either way, so this is answered without a billed round trip.
            return []

        import voyageai  # noqa: PLC0415 - TID253: heavy lib, slow startup

        client = voyageai.Client(api_key=self._api_key, timeout=self._timeout, max_retries=VOYAGE_MAX_RETRIES)
        response = client.rerank(
            query=query,
            documents=documents,
            model=self._model,
            top_k=min(limit, len(documents)),
            # Clip the pair to the model's context window rather than have the call rejected
            # outright. Documents are chunk-sized and the query is bounded by its caller (see
            # `apps.documents.retrieval`), so this is a guard, not the normal path.
            truncation=True,
        )
        return [RerankedDocument(index=result.index, score=result.relevance_score) for result in response.results]

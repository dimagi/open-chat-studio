"""Collection retrieval: dense (vector) search, lexical (full-text) search, and their fusion.

`search_collection` is the single entry point used by the chat search tools and the
collection query preview, so both share one definition of "what retrieval means".

When the `flag_hybrid_search` flag is inactive for a collection's team, retrieval is
dense-only and returns exactly what it did before hybrid search existed. When it is active,
a lexical ranking from Postgres full-text search is fused with the dense ranking using
Reciprocal Rank Fusion (RRF).

RRF fuses *ranks*, not scores, on purpose: cosine distances and `ts_rank_cd` values live on
incomparable scales, so score-level fusion would need brittle per-query normalization.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence

from django.conf import settings
from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import F
from pgvector.django import CosineDistance

from apps.documents.models import Collection, chunk_from_indexed_file
from apps.files.models import FileChunkEmbedding

# Fields every caller of `search_collection` relies on. Kept as one superset so the deferred
# field set does not silently differ between call sites and trigger per-row queries later.
_RESULT_ONLY_FIELDS = ("text", "file__name", "file__metadata")


def search_collection(
    collection: Collection,
    query: str,
    top_k: int = 5,
    *,
    query_vector: list[float] | None = None,
) -> list[FileChunkEmbedding]:
    """Return the `top_k` chunks of `collection` most relevant to `query`.

    Args:
        collection: The collection to search.
        query: The natural-language search query.
        top_k: Number of chunks to return.
        query_vector: Precomputed embedding of `query`. Pass this when an index manager is
            already on hand, to avoid rebuilding one just to embed the query.

    Returns:
        Chunks ordered most relevant first. `text`, `file.name` and `file.metadata` are
        loaded; other fields are deferred.
    """
    if query_vector is None:
        query_vector = collection.get_query_vector(query)

    if not collection.hybrid_search_enabled:
        return list(_dense_queryset(collection, query_vector, top_k))

    # fetch_k widens the candidate pool for fusion; it must never narrow the result set below
    # what the caller asked for, which a per-collection override lower than top_k would do.
    fetch_k = max(top_k, collection.search_fetch_k)
    # `values_list` drops the select_related/only from the shared dense queryset, so this
    # fetches ids alone -- no join -- while keeping one definition of the dense ranking.
    dense_ids = list(_dense_queryset(collection, query_vector, fetch_k).values_list("id", flat=True))
    lexical_ids = _lexical_candidate_ids(collection, query, fetch_k)

    if not lexical_ids:
        # Nothing to fuse: the query had no searchable lexical terms, or matched no chunk.
        # Truncating the dense candidates preserves the dense-only ordering exactly.
        return _load_chunks_in_order(dense_ids[:top_k])

    dense_weight = collection.search_dense_weight
    fused_ids = reciprocal_rank_fusion(
        [dense_ids, lexical_ids],
        weights=[dense_weight, 1 - dense_weight],
    )
    return _load_chunks_in_order(fused_ids[:top_k])


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[int]],
    weights: Sequence[float],
    k: int | None = None,
) -> list[int]:
    """Fuse ranked ID lists into one ranking by weighted Reciprocal Rank Fusion.

    Each list contributes `weight / (k + rank)` to every ID it contains, where `rank` is
    1-based. An ID missing from a list simply receives no contribution from it, which is how
    one-sided candidates are handled -- no imputation required.

    Args:
        ranked_lists: Ranked ID lists, best first. IDs may repeat across lists.
        weights: One weight per list, positionally matched.
        k: RRF smoothing constant. Larger values flatten the influence of top ranks.
            Defaults to `settings.DOCUMENT_SEARCH_RRF_K`.

    Returns:
        Deduplicated IDs ordered by descending fused score. Ties break on ascending ID so
        results are deterministic for a given candidate set.
    """
    if k is None:
        k = settings.DOCUMENT_SEARCH_RRF_K

    scores: dict[int, float] = defaultdict(float)
    for ranked_ids, weight in zip(ranked_lists, weights, strict=True):
        seen: set[int] = set()
        for rank, chunk_id in enumerate(ranked_ids, start=1):
            # A single list should not pay twice for the same chunk; keep its best rank.
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            scores[chunk_id] += weight / (k + rank)

    return sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))


def _dense_queryset(collection: Collection, query_vector: list[float], limit: int):
    """Chunks ranked by cosine distance between their embedding and the query embedding."""
    return (
        FileChunkEmbedding.objects.annotate(distance=CosineDistance("embedding", query_vector))
        .filter(collection_id=collection.id)
        .filter(chunk_from_indexed_file())
        .order_by("distance")
        .select_related("file")
        .only(*_RESULT_ONLY_FIELDS)[:limit]
    )


def _lexical_candidate_ids(collection: Collection, query: str, limit: int) -> list[int]:
    """IDs of chunks matching `query` lexically, best first.

    Uses `websearch_to_tsquery`, which accepts arbitrary user input -- quotes, `or`, and `-`
    are interpreted as search syntax and anything it cannot parse is ignored rather than
    raising. A query that reduces to no lexical terms (stopwords, punctuation, emoji) matches
    nothing, so the caller falls back to dense-only ordering.
    """
    if not query or not query.strip():
        return []

    search_query = SearchQuery(query, config=settings.DOCUMENT_SEARCH_FTS_CONFIG, search_type="websearch")
    return list(
        FileChunkEmbedding.objects.filter(collection_id=collection.id)
        .filter(chunk_from_indexed_file())
        .filter(search_vector=search_query)
        # ts_rank_cd (cover density) rewards matches that occur close together.
        .annotate(rank=SearchRank(F("search_vector"), search_query, cover_density=True))
        .order_by("-rank", "id")
        .values_list("id", flat=True)[:limit]
    )


def _load_chunks_in_order(chunk_ids: Iterable[int]) -> list[FileChunkEmbedding]:
    """Fetch chunks by ID, preserving the given order.

    Postgres does not guarantee ordering for an `id__in` lookup, so the fused ranking is
    reapplied in Python.
    """
    chunk_ids = list(chunk_ids)
    if not chunk_ids:
        return []

    chunks_by_id = {
        chunk.id: chunk
        for chunk in FileChunkEmbedding.objects.filter(id__in=chunk_ids)
        .select_related("file")
        .only(*_RESULT_ONLY_FIELDS)
    }
    return [chunks_by_id[chunk_id] for chunk_id in chunk_ids if chunk_id in chunks_by_id]

"""Collection retrieval: dense (vector) search, lexical (full-text) search, fusion, and reranking.

`search_collection` is the single entry point used by the chat search tools and the
collection query preview, so both share one definition of "what retrieval means".

With both feature flags inactive for a collection's team, retrieval is dense-only and returns
exactly what it did before either stage existed. The two stages are independent, and each falls
back to what sits beneath it:

`flag_hybrid_search` adds a lexical ranking from Postgres full-text search, fused with the dense
ranking by Reciprocal Rank Fusion (RRF). RRF fuses *ranks*, not scores, on purpose: cosine
distances and `ts_rank_cd` values live on incomparable scales, so score-level fusion would need
brittle per-query normalization. A query with no lexical hits leaves the dense ranking as it is.

`flag_reranking` rescores a wider pool of whatever candidates the stages above produced --
dense-only or fused -- against the query, and returns the best `top_k`. A reranker that fails or
answers with something unusable leaves the ranking it was given as it is.
"""

import functools
import logging
import operator
import time
from collections import defaultdict
from collections.abc import Iterable, Sequence

from django.conf import settings
from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import F
from pgvector.django import CosineDistance

from apps.documents.models import Collection, SearchLanguage, chunk_from_indexed_file
from apps.documents.rerankers import RerankedDocument, Reranker
from apps.files.models import FileChunkEmbedding

logger = logging.getLogger("ocs.retrieval")

# Fields every caller of `search_collection` relies on. Kept as one superset so the deferred
# field set does not silently differ between call sites and trigger per-row queries later.
# `context` is here for `contextualized_text`, which is what the rerank stage scores: leaving it
# deferred would turn one query into one per candidate the moment reranking was switched on.
_RESULT_ONLY_FIELDS = ("text", "context", "file__name", "file__metadata")

# Upper bound on the conversation context prepended to the reranker's query. Voyage clips the
# query-document pair to the model's context window, so an unbounded context would eat the room
# the chunk needs and leave the reranker scoring the query against a truncated document.
MAX_RERANK_CONTEXT_CHARS = 1500


def search_collection(
    collection: Collection,
    query: str,
    top_k: int = 5,
    *,
    query_vector: list[float] | None = None,
    context: str | None = None,
) -> list[FileChunkEmbedding]:
    """Return the `top_k` chunks of `collection` most relevant to `query`.

    Args:
        collection: The collection to search.
        query: The natural-language search query.
        top_k: Number of chunks to return.
        query_vector: Precomputed embedding of `query`. Pass this when an index manager is
            already on hand, to avoid rebuilding one just to embed the query.
        context: Recent conversation turns, when the caller has them. Used only to condition the
            reranker's view of the query, so it changes nothing unless reranking is active for
            this collection.

    Returns:
        Chunks ordered most relevant first. `text`, `context`, `file.name` and `file.metadata`
        are loaded; other fields are deferred.
    """
    if query_vector is None:
        query_vector = collection.get_query_vector(query)

    reranker = collection.get_reranker()
    # A reranker can only improve on the ranking it is given if it is given more than the caller
    # asked for; `rerank_top_n` is that pool. With no reranker nothing widens, and the queries
    # below are exactly the ones hybrid search has always run.
    candidate_count = max(top_k, collection.rerank_top_n) if reranker else top_k
    candidates = _retrieve_candidates(collection, query, query_vector, candidate_count)
    if reranker is None:
        return candidates
    return _rerank(reranker, query, candidates, top_k, context=context)


def _retrieve_candidates(
    collection: Collection,
    query: str,
    query_vector: list[float],
    top_k: int,
) -> list[FileChunkEmbedding]:
    """The best `top_k` chunks by dense search alone, or by dense and lexical search fused."""
    if not collection.hybrid_search_enabled:
        return list(_dense_queryset(collection, query_vector, top_k))

    # fetch_k widens the candidate pool for fusion; it must never narrow the result set below
    # what the caller asked for, which a per-collection override lower than top_k would do.
    fetch_k = max(top_k, collection.search_fetch_k)
    # Lexical runs first because it is the branch that can come back empty. When it does there is
    # nothing to fuse, and returning the dense queryset directly costs one query instead of two
    # and keeps the `distance` annotation that the collection query preview renders.
    lexical_ids = _lexical_candidate_ids(collection, query, fetch_k)
    if not lexical_ids:
        return list(_dense_queryset(collection, query_vector, top_k))

    # `values_list` drops the select_related/only from the shared dense queryset, so this
    # fetches ids alone -- no join -- while keeping one definition of the dense ranking.
    dense_ids = list(_dense_queryset(collection, query_vector, fetch_k).values_list("id", flat=True))
    dense_weight = collection.search_dense_weight
    scores = _rrf_scores([dense_ids, lexical_ids], weights=[dense_weight, 1 - dense_weight])
    fused_ids = _rank_by_score(scores)[:top_k]
    return _load_chunks_in_order(fused_ids, scores)


def _rerank(
    reranker: Reranker,
    query: str,
    candidates: list[FileChunkEmbedding],
    top_k: int,
    *,
    context: str | None,
) -> list[FileChunkEmbedding]:
    """Reorder `candidates` by the reranker's scores and keep the best `top_k`.

    Every failure path returns the candidates in the order retrieval already put them in, cut to
    `top_k`. Reranking is a quality stage on top of a ranking that is already usable, so a
    reranker that errors, times out, or answers with something that does not describe the list it
    was sent must cost the search its improvement, not its results.

    Chunks that were reranked carry a `rerank_score`; on a fallback path they do not, which is
    how the collection query preview knows which number it is looking at.
    """
    if top_k <= 0:
        # A caller that wants nothing back gets it without a billed call.
        return []
    if len(candidates) <= 1:
        # A single candidate cannot be reordered, and the provider would still bill the call.
        return candidates[:top_k]

    query = query.strip()
    if not query:
        # Nothing to score the candidates against. Dense search still ranks a blank query by
        # whatever its embedding came out as, but a reranker has no such fallback.
        return candidates[:top_k]

    started = time.monotonic()
    try:
        ranked = reranker.rerank(
            _rerank_query(query, context),
            [candidate.contextualized_text for candidate in candidates],
            limit=top_k,
        )
    except Exception:
        logger.exception(
            "Reranking failed; falling back to the un-reranked ranking",
            extra={"candidate_count": len(candidates), "top_k": top_k},
        )
        return candidates[:top_k]

    if not _ranking_describes_candidates(ranked, len(candidates)):
        return candidates[:top_k]

    logger.info(
        "Reranked collection retrieval candidates",
        extra={
            "candidate_count": len(candidates),
            "result_count": len(ranked),
            "duration_ms": round((time.monotonic() - started) * 1000),
        },
    )
    results = []
    for item in ranked:
        chunk = candidates[item.index]
        chunk.rerank_score = item.score
        results.append(chunk)
    return results


def _rerank_query(query: str, context: str | None) -> str:
    """The query representation handed to the reranker.

    `context` is the caller's conversation context (the context conditioning asked for in issue
    #2681). Prepending it lets the reranker tell which of several superficially similar chunks
    answers the question actually being asked -- "how much does it cost" scores differently once
    the turn before it is visible. It is clipped to its tail because the recent turns are the
    ones that disambiguate, and the query goes last and is never clipped here: it is the half the
    reranker has to see in full.
    """
    if not context or not context.strip():
        return query
    return f"{context.strip()[-MAX_RERANK_CONTEXT_CHARS:]}\n\n{query}"


def _ranking_describes_candidates(ranked: list[RerankedDocument], candidate_count: int) -> bool:
    """Whether a reranker's answer can be applied to the candidate list as it stands.

    An index outside the list, or the same index twice, means the answer does not describe what
    was sent: the first would raise on lookup and the second would put one chunk into the prompt
    twice. Neither is worth trying to repair, and both are quieter than they look -- without this
    the duplicate would simply ship.
    """
    indices = [item.index for item in ranked]
    if not indices:
        # `top_k` is at least 1 by the time this is reached, so an answer naming none of the
        # candidates is not "fewer results than asked for" -- it does not describe them at all.
        logger.warning(
            "Reranker ranked none of the candidates; falling back to the un-reranked ranking",
            extra={"candidate_count": candidate_count},
        )
        return False
    if any(not 0 <= index < candidate_count for index in indices):
        logger.warning(
            "Reranker returned an out-of-range candidate index; falling back to the un-reranked ranking",
            extra={"candidate_count": candidate_count, "indices": indices},
        )
        return False
    if len(set(indices)) != len(indices):
        logger.warning(
            "Reranker returned the same candidate more than once; falling back to the un-reranked ranking",
            extra={"candidate_count": candidate_count, "indices": indices},
        )
        return False
    return True


def _rrf_scores(
    ranked_lists: Sequence[Sequence[int]],
    weights: Sequence[float],
    k: int | None = None,
) -> dict[int, float]:
    """Weighted Reciprocal Rank Fusion: the fused score for each ID.

    Each list contributes `weight / (k + rank)` to every ID it contains, where `rank` is
    1-based. An ID missing from a list simply receives no contribution from it, which is how
    one-sided candidates are handled -- no imputation required.

    Kept separate from the ranking so callers can surface the score as well as the order; the
    collection query preview renders it.

    Args:
        ranked_lists: Ranked ID lists, best first. IDs may repeat across lists.
        weights: One weight per list, positionally matched.
        k: RRF smoothing constant. Larger values flatten the influence of top ranks.
            Defaults to `settings.DOCUMENT_SEARCH_RRF_K`.
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
    return scores


def _rank_by_score(scores: dict[int, float]) -> list[int]:
    """Order IDs by descending score, breaking ties on ascending ID for determinism."""
    return sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))


def _dense_queryset(collection: Collection, query_vector: list[float], limit: int):
    """Chunks ranked by cosine distance between their embedding and the query embedding.

    Ties break on id, as the lexical side does, so equally distant chunks come back in the same
    order every time rather than in whatever order Postgres happens to produce.
    """
    return (
        FileChunkEmbedding.objects.annotate(distance=CosineDistance("embedding", query_vector))
        .filter(collection_id=collection.id)
        .filter(chunk_from_indexed_file())
        .order_by("distance", "id")
        .select_related("file")
        .only(*_RESULT_ONLY_FIELDS)[:limit]
    )


def _lexical_search_query(query: str, config: str) -> SearchQuery | None:
    """Parse `query` into a tsquery, choosing AND or OR semantics to suit the configuration.

    Under a language configuration, stopwords are stripped and words are stemmed at parse time,
    so OR-combining the remaining terms is safe and is what makes multi-word questions work:
    requiring every term (websearch's AND) fails as soon as one word is absent from a chunk.

    Under `simple` nothing is stripped or stemmed, so every stopword survives as a lexeme. ORing
    those would match almost everything, and `ts_rank_cd` has no inverse document frequency to
    discount them, so a chunk full of "what/is/the" outranks the one that answers the question.
    `simple` therefore keeps websearch's AND semantics: exact tokens only, which is the honest
    behaviour when the language is unknown.

    Returns None when the query has no searchable terms, so the caller can skip the query
    entirely and fall back to dense-only ordering.
    """
    if not query or not query.strip():
        return None

    if config == SearchLanguage.SIMPLE:
        # `websearch_to_tsquery` also accepts quoted phrases and `-exclusions` from the user.
        return SearchQuery(query, config=config, search_type="websearch")

    terms = [SearchQuery(term, config=config, search_type="plain") for term in query.split()]
    if not terms:
        return None
    # `|` compiles to `tsquery || tsquery`, fully parameterised. Terms that reduce to nothing
    # (stopwords) contribute an empty tsquery and drop out harmlessly.
    return functools.reduce(operator.or_, terms)


def _lexical_candidate_ids(collection: Collection, query: str, limit: int) -> list[int]:
    """IDs of chunks matching `query` lexically, best first.

    The query is parsed with the collection's own search configuration, which is the same one
    its chunks were indexed with. A mismatch here matches nothing at all, and looks exactly like
    a query with no lexical hits.
    """
    search_query = _lexical_search_query(query, collection.search_language)
    if search_query is None:
        return []

    return list(
        FileChunkEmbedding.objects.filter(collection_id=collection.id)
        .filter(chunk_from_indexed_file())
        .filter(search_vector=search_query)
        # ts_rank_cd (cover density) rewards matches that occur close together.
        .annotate(rank=SearchRank(F("search_vector"), search_query, cover_density=True))
        .order_by("-rank", "id")
        .values_list("id", flat=True)[:limit]
    )


def _load_chunks_in_order(chunk_ids: Iterable[int], scores: dict[int, float] | None = None) -> list[FileChunkEmbedding]:
    """Fetch chunks by ID, preserving the given order.

    Postgres does not guarantee ordering for an `id__in` lookup, so the fused ranking is
    reapplied in Python. The fused score is attached to each chunk as `fused_score`, since the
    re-fetch drops the `distance` annotation the dense queryset carries and the collection query
    preview needs something to show.
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
    ordered = [chunks_by_id[chunk_id] for chunk_id in chunk_ids if chunk_id in chunks_by_id]
    for chunk in ordered:
        chunk.fused_score = (scores or {}).get(chunk.id)
    return ordered

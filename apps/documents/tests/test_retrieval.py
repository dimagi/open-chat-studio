from types import SimpleNamespace
from unittest import mock

import pytest
from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.template.loader import render_to_string
from django.test.utils import CaptureQueriesContext
from waffle.testutils import override_flag

from apps.documents.models import CollectionFile, FileStatus, SearchLanguage
from apps.documents.rerankers import RerankedDocument, Reranker, VoyageReranker
from apps.documents.retrieval import (
    MAX_RERANK_CONTEXT_CHARS,
    _lexical_candidate_ids,
    _rank_by_score,
    _rrf_scores,
    search_collection,
)
from apps.documents.views import _result_score_kind
from apps.service_providers.llm_service.index_managers import LocalIndexManager
from apps.service_providers.models import LlmProviderTypes
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.files import FileChunkEmbeddingFactory, FileFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory

HYBRID_FLAG = "flag_hybrid_search"
RERANK_FLAG = "flag_reranking"


def _fuse(ranked_lists, weights, k=None) -> list[int]:
    """Score then rank, which is what `search_collection` does with the two separately.

    It needs the scores as well as the order, so there is no single production function to call
    here; composing them in the tests keeps the assertions about ordering readable.
    """
    return _rank_by_score(_rrf_scores(ranked_lists, weights, k))


class TestReciprocalRankFusion:
    """RRF is pure ranking arithmetic, so it is tested without touching the database."""

    def test_fuses_two_lists_by_rank(self):
        # b is 2nd then 1st, a is 1st then 3rd. With equal weights b's combined rank wins.
        fused = _fuse([[1, 2, 3], [2, 3, 1]], weights=[0.5, 0.5], k=60)
        assert fused == [2, 1, 3]

    def test_dense_weight_decides_disjoint_winner(self):
        """With no overlap, the top of the heavier-weighted list must rank first."""
        dense_first = _fuse([[1], [2]], weights=[0.7, 0.3], k=60)
        lexical_first = _fuse([[1], [2]], weights=[0.3, 0.7], k=60)
        assert dense_first == [1, 2]
        assert lexical_first == [2, 1]

    @pytest.mark.parametrize(
        ("ranked_lists", "weights", "expected"),
        [
            pytest.param([[7]], [1.0], [7], id="single-list-passthrough"),
            pytest.param([[], []], [0.7, 0.3], [], id="both-empty"),
            pytest.param([[5, 6], []], [0.7, 0.3], [5, 6], id="lexical-empty-keeps-dense-order"),
            pytest.param([[], [5, 6]], [0.7, 0.3], [5, 6], id="dense-empty-keeps-lexical-order"),
            pytest.param([[1, 2], [1, 2]], [0.5, 0.5], [1, 2], id="identical-lists-preserve-order"),
        ],
    )
    def test_one_sided_and_empty_candidates(self, ranked_lists, weights, expected):
        assert _fuse(ranked_lists, weights=weights, k=60) == expected

    def test_deduplicates_across_and_within_lists(self):
        """An id repeated inside one list must not be scored twice, and must appear once."""
        fused = _fuse([[1, 1, 2], [1]], weights=[0.5, 0.5], k=60)
        assert fused == [1, 2]
        assert len(fused) == len(set(fused))

    def test_repeated_id_keeps_its_best_rank(self):
        """A duplicate later in a list must not drag its own score down."""
        with_duplicate = _fuse([[1, 2, 1]], weights=[1.0], k=60)
        without_duplicate = _fuse([[1, 2]], weights=[1.0], k=60)
        assert with_duplicate == without_duplicate == [1, 2]

    def test_k_trades_rank_influence_against_weight(self):
        """A small k lets a top rank beat a heavier weight; a large k flattens ranks so weight wins.

        The lists are disjoint on purpose: an id appearing in both always benefits from two
        contributions, which would mask the effect of k.
        """
        dense_ids = list(range(1, 11))  # ranks 1..10, the heavier weight
        lexical_ids = [99]  # rank 1, the lighter weight
        weights = [0.7, 0.3]

        small_k = _fuse([dense_ids, lexical_ids], weights=weights, k=1)
        large_k = _fuse([dense_ids, lexical_ids], weights=weights, k=100_000)

        # k=1: rank 1 in the lighter list still outranks the dense tail.
        assert small_k.index(99) < small_k.index(10)
        # k=100_000: rank differences are negligible, so the heavier weight wins outright.
        assert large_k.index(99) > large_k.index(10)
        assert large_k[-1] == 99

    def test_ties_break_deterministically_on_id(self):
        """Equal scores must produce a stable order regardless of input ordering."""
        assert _fuse([[3, 1, 2]], weights=[0.0], k=60) == [1, 2, 3]
        assert _fuse([[2, 3, 1]], weights=[0.0], k=60) == [1, 2, 3]

    def test_defaults_k_to_setting(self):
        explicit = _fuse([[1, 2], [2]], weights=[0.5, 0.5], k=settings.DOCUMENT_SEARCH_RRF_K)
        assert _fuse([[1, 2], [2]], weights=[0.5, 0.5]) == explicit

    def test_mismatched_weights_are_rejected(self):
        """A weight-per-list mismatch is a programming error, not something to silently absorb."""
        with pytest.raises(ValueError, match="zip"):
            _fuse([[1], [2]], weights=[1.0])


def _make_indexed_collection(**kwargs):
    """A collection whose chunks all belong to a cleanly indexed file."""
    collection = CollectionFactory.create(is_index=True, is_remote_index=False, **kwargs)
    file = FileFactory.create(team=collection.team)
    CollectionFile.objects.create(collection=collection, file=file, status=FileStatus.COMPLETED)
    return collection, file


def _add_chunk(collection, file, text, embedding, context=""):
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


def _unit_vector(index: int) -> list[float]:
    """A one-hot embedding, so cosine similarity between distinct vectors is controllable."""
    vector = [0.0] * settings.EMBEDDING_VECTOR_SIZE
    vector[index] = 1.0
    return vector


@pytest.mark.django_db()
class TestSearchCollection:
    def test_flag_off_returns_dense_only_ordering(self):
        """Regression guarantee: with the flag off, results match the dense-only query exactly."""
        collection, file = _make_indexed_collection()
        near = _add_chunk(collection, file, "totally unrelated prose", _unit_vector(0))
        far = _add_chunk(collection, file, "quokka", _unit_vector(1))

        # Query vector aligns with `near`, and the lexical term only appears in `far`.
        with mock.patch.object(type(collection), "get_query_vector", return_value=_unit_vector(0)):
            with override_flag(HYBRID_FLAG, active=False):
                results = search_collection(collection, "quokka", top_k=2)

        assert [chunk.id for chunk in results] == [near.id, far.id]

    def test_hybrid_surfaces_keyword_match_dense_search_misses(self):
        """The point of hybrid search: an exact keyword hit that the embedding does not rank."""
        collection, file = _make_indexed_collection()
        dense_hit = _add_chunk(collection, file, "totally unrelated prose", _unit_vector(0))
        keyword_hit = _add_chunk(collection, file, "the quokka is a small macropod", _unit_vector(1))

        with mock.patch.object(type(collection), "get_query_vector", return_value=_unit_vector(0)):
            with override_flag(HYBRID_FLAG, active=True):
                results = search_collection(collection, "quokka", top_k=1)

        assert [chunk.id for chunk in results] == [keyword_hit.id]
        assert dense_hit.id not in {chunk.id for chunk in results}

    def test_multi_word_question_promotes_the_keyword_match(self):
        """End to end version of the multi-word case, through the tool's actual entry point.

        The LLM search tool passes a natural-language question, not a single keyword, so this is
        the shape of every real call.
        """
        collection, file = _make_indexed_collection(search_language=SearchLanguage.ENGLISH)
        dense_hit = _add_chunk(collection, file, "totally unrelated prose", _unit_vector(0))
        answer = _add_chunk(collection, file, "Paris is the capital of France.", _unit_vector(1))

        with mock.patch.object(type(collection), "get_query_vector", return_value=_unit_vector(0)):
            with override_flag(HYBRID_FLAG, active=True):
                results = search_collection(collection, "what is the capital of France", top_k=1)

        assert [chunk.id for chunk in results] == [answer.id]
        assert dense_hit.id not in {chunk.id for chunk in results}

    def test_hybrid_still_returns_dense_hit_when_lexical_misses(self):
        """A query with no lexical match degrades gracefully to the dense ordering."""
        collection, file = _make_indexed_collection()
        dense_hit = _add_chunk(collection, file, "totally unrelated prose", _unit_vector(0))

        with mock.patch.object(type(collection), "get_query_vector", return_value=_unit_vector(0)):
            with override_flag(HYBRID_FLAG, active=True):
                results = search_collection(collection, "nonexistentterm", top_k=5)

        assert [chunk.id for chunk in results] == [dense_hit.id]

    @pytest.mark.parametrize(
        "query",
        [
            pytest.param("", id="empty"),
            pytest.param("   ", id="whitespace"),
            pytest.param("!!! ??? ...", id="punctuation-only"),
            pytest.param("🙂🙂", id="emoji-only"),
            pytest.param('"unclosed quote', id="unbalanced-quote"),
            pytest.param("and or not -", id="operators-only"),
        ],
    )
    def test_untokenizable_queries_fall_back_to_dense(self, query):
        """Arbitrary user input must never raise; it degrades to dense-only ordering."""
        collection, file = _make_indexed_collection()
        chunk = _add_chunk(collection, file, "some indexed prose", _unit_vector(0))

        with mock.patch.object(type(collection), "get_query_vector", return_value=_unit_vector(0)):
            with override_flag(HYBRID_FLAG, active=True):
                results = search_collection(collection, query, top_k=5)

        assert [result.id for result in results] == [chunk.id]

    def test_context_is_searched_lexically(self):
        """Contextual retrieval headers (phase 1) must be lexically searchable too."""
        collection, file = _make_indexed_collection()
        chunk = _add_chunk(
            collection,
            file,
            "the animal is nocturnal",
            _unit_vector(5),
            context="This passage is about the quokka.",
        )

        with mock.patch.object(type(collection), "get_query_vector", return_value=_unit_vector(0)):
            with override_flag(HYBRID_FLAG, active=True):
                results = search_collection(collection, "quokka", top_k=5)

        assert [result.id for result in results] == [chunk.id]

    def test_excludes_chunks_of_unindexed_files(self):
        """A file that failed indexing must not leak chunks through either branch."""
        collection, good_file = _make_indexed_collection()
        good = _add_chunk(collection, good_file, "quokka facts", _unit_vector(0))

        failed_file = FileFactory.create(team=collection.team)
        CollectionFile.objects.create(collection=collection, file=failed_file, status=FileStatus.FAILED)
        failed_chunk = _add_chunk(collection, failed_file, "quokka facts", _unit_vector(0))

        with mock.patch.object(type(collection), "get_query_vector", return_value=_unit_vector(0)):
            with override_flag(HYBRID_FLAG, active=True):
                results = search_collection(collection, "quokka", top_k=5)

        result_ids = {result.id for result in results}
        assert good.id in result_ids
        assert failed_chunk.id not in result_ids

    def test_does_not_leak_across_collections(self):
        collection, file = _make_indexed_collection()
        mine = _add_chunk(collection, file, "quokka facts", _unit_vector(0))
        other_collection, other_file = _make_indexed_collection()
        theirs = _add_chunk(other_collection, other_file, "quokka facts", _unit_vector(0))

        with mock.patch.object(type(collection), "get_query_vector", return_value=_unit_vector(0)):
            with override_flag(HYBRID_FLAG, active=True):
                results = search_collection(collection, "quokka", top_k=5)

        result_ids = {result.id for result in results}
        assert mine.id in result_ids
        assert theirs.id not in result_ids

    def test_respects_top_k(self):
        collection, file = _make_indexed_collection()
        for index in range(5):
            _add_chunk(collection, file, f"quokka chunk {index}", _unit_vector(index))

        with mock.patch.object(type(collection), "get_query_vector", return_value=_unit_vector(0)):
            with override_flag(HYBRID_FLAG, active=True):
                results = search_collection(collection, "quokka", top_k=2)

        assert len(results) == 2

    def test_fetch_k_below_top_k_still_returns_top_k(self):
        """A per-collection fetch_k smaller than top_k must not truncate the result set.

        fetch_k widens the candidate pool for fusion; it is not a cap on what the caller asked
        for. The node's max_results (top_k) is what controls the final count.
        """
        collection, file = _make_indexed_collection(search_fetch_k=2)
        for index in range(5):
            _add_chunk(collection, file, f"quokka chunk {index}", _unit_vector(index))

        with mock.patch.object(type(collection), "get_query_vector", return_value=_unit_vector(0)):
            with override_flag(HYBRID_FLAG, active=True):
                results = search_collection(collection, "quokka", top_k=5)

        assert len(results) == 5

    def test_remote_index_never_uses_hybrid(self):
        """Remote index chunks live at the provider, so there is no local lexical index."""
        collection = CollectionFactory.create(is_index=True, is_remote_index=True)
        with override_flag(HYBRID_FLAG, active=True):
            assert collection.hybrid_search_enabled is False

    def test_team_screen_enabled_flag_reaches_hybrid_search(self, team_flag):
        """A flag row shaped the way the team settings screen writes it must enable hybrid search.

        The screen creates the row with `everyone=None` and adds the team to the M2M (#4321);
        only an explicit `True`/`False` is a global decision.
        """
        collection = CollectionFactory.create(is_index=True)
        team_flag(HYBRID_FLAG, collection.team)
        assert collection.hybrid_search_enabled is True

    def test_provided_query_vector_avoids_recomputing_the_embedding(self):
        """The preview path passes a vector it already has; the service must not embed again."""
        collection, file = _make_indexed_collection()
        chunk = _add_chunk(collection, file, "indexed prose", _unit_vector(0))

        with mock.patch.object(type(collection), "get_query_vector") as get_query_vector:
            with override_flag(HYBRID_FLAG, active=False):
                results = search_collection(collection, "prose", top_k=1, query_vector=_unit_vector(0))

        get_query_vector.assert_not_called()
        assert [result.id for result in results] == [chunk.id]

    def test_per_collection_values_are_used(self):
        collection = CollectionFactory.create(search_dense_weight=0.25, search_fetch_k=7)
        assert collection.search_dense_weight == 0.25
        assert collection.search_fetch_k == 7

    def test_new_collections_get_the_default_knobs(self):
        """Every collection holds a usable value, so callers read the field directly."""
        collection = CollectionFactory.create()
        assert collection.search_dense_weight == 0.7
        assert collection.search_fetch_k == 40


@pytest.mark.django_db()
class TestLexicalSearchLanguage:
    """The configuration used to build a chunk's vector and to parse the query must agree, and it
    decides whether a multi-word question can match at all.
    """

    ANSWER = "Paris is the capital of France."
    QUESTION = "what is the capital of France"

    def test_multi_word_question_matches_under_a_language_config(self):
        """The regression this suite previously missed entirely.

        Every earlier test used a single-token query, which satisfies websearch's AND trivially.
        A real question is several words, and under a language config the stopwords are stripped
        at parse time and the rest are stemmed, so it matches.
        """
        collection, file = _make_indexed_collection(search_language=SearchLanguage.ENGLISH)
        chunk = _add_chunk(collection, file, self.ANSWER, _unit_vector(0))

        assert _lexical_candidate_ids(collection, self.QUESTION, 10) == [chunk.id]

    def test_partially_matching_question_still_matches_under_a_language_config(self):
        """OR-combining, specifically. Requiring every term (websearch's AND) fails as soon as one
        word of the question is absent from the chunk, which is the normal case for a real
        question. Only the terms the chunk does contain should be needed.
        """
        collection, file = _make_indexed_collection(search_language=SearchLanguage.ENGLISH)
        chunk = _add_chunk(collection, file, "Paris is the capital of France. Population 2.1 million.", _unit_vector(0))

        # "2024" and "census" appear nowhere in the chunk; AND semantics would return nothing.
        assert _lexical_candidate_ids(collection, "capital of France population 2024 census", 10) == [chunk.id]

    def test_simple_keeps_and_semantics(self):
        """`simple` strips nothing, so every stopword must be present. That is precise but weak,
        and is the honest behaviour when the collection's language is unknown. It must not be
        quietly turned into an OR, which would rank stopword-heavy chunks first.
        """
        collection, file = _make_indexed_collection(search_language=SearchLanguage.SIMPLE)
        chunk = _add_chunk(collection, file, self.ANSWER, _unit_vector(0))

        assert _lexical_candidate_ids(collection, self.QUESTION, 10) == []
        # The exact tokens it does hold still match.
        assert _lexical_candidate_ids(collection, "capital France", 10) == [chunk.id]

    @pytest.mark.parametrize(
        ("language", "document", "question"),
        [
            pytest.param(SearchLanguage.ENGLISH, "Paris is the capital of France.", "capitals of France", id="english"),
            pytest.param(
                SearchLanguage.SPANISH, "Paris es la capital de Francia.", "cual es la capital de Francia", id="spanish"
            ),
            pytest.param(
                SearchLanguage.RUSSIAN, "Париж — столица Франции.", "какая столица Франции", id="russian-non-latin"
            ),
        ],
    )
    def test_round_trip_per_language(self, language, document, question):
        """Stemming and stopword handling differ per configuration, including non-Latin scripts."""
        collection, file = _make_indexed_collection(search_language=language)
        chunk = _add_chunk(collection, file, document, _unit_vector(0))

        assert _lexical_candidate_ids(collection, question, 10) == [chunk.id]

    def test_changing_the_language_after_indexing_finds_nothing(self):
        """The drift trap: chunks keep the configuration they were indexed with.

        A collection indexed as Spanish but queried as English returns no lexical hits, which is
        indistinguishable from a query that simply has none. Re-indexing rebuilds the vectors with
        the current language; nothing else does, which is why the field's help text says so.
        """
        collection, file = _make_indexed_collection(search_language=SearchLanguage.SPANISH)
        chunk = _add_chunk(collection, file, "Paris es la capital de Francia.", _unit_vector(0))

        collection.search_language = SearchLanguage.ENGLISH
        collection.save()
        assert _lexical_candidate_ids(collection, "capital of Francia", 10) == []

        # Re-indexing the chunk under the new language brings it back.
        LocalIndexManager._build_search_vectors([chunk], collection)
        assert _lexical_candidate_ids(collection, "capital of Francia", 10) == [chunk.id]

    def test_stopword_only_query_falls_back_to_dense(self):
        """Every term is stripped, so the tsquery is empty and matches nothing."""
        collection, file = _make_indexed_collection(search_language=SearchLanguage.ENGLISH)
        chunk = _add_chunk(collection, file, self.ANSWER, _unit_vector(0))

        assert _lexical_candidate_ids(collection, "what is the", 10) == []

        with mock.patch.object(type(collection), "get_query_vector", return_value=_unit_vector(0)):
            with override_flag(HYBRID_FLAG, active=True):
                results = search_collection(collection, "what is the", top_k=5)
        assert [result.id for result in results] == [chunk.id]


@pytest.mark.django_db()
class TestQueryPreviewScore:
    """The preview renders a per-chunk number. The hybrid path re-fetches chunks by id, which
    drops the CosineDistance annotation, so it has to carry the fused score instead.
    """

    def test_hybrid_results_carry_a_fused_score(self):
        collection, file = _make_indexed_collection(search_language=SearchLanguage.ENGLISH)
        _add_chunk(collection, file, "Paris is the capital of France.", _unit_vector(0))

        with mock.patch.object(type(collection), "get_query_vector", return_value=_unit_vector(0)):
            with override_flag(HYBRID_FLAG, active=True):
                results = search_collection(collection, "capital of France", top_k=5)

        # Assert the count first: `all()` over an empty list is True, so without this the test
        # would still pass if retrieval returned nothing at all.
        assert len(results) == 1
        assert all(result.fused_score is not None and result.fused_score > 0 for result in results)

    def test_dense_only_results_keep_their_distance(self):
        collection, file = _make_indexed_collection(search_language=SearchLanguage.ENGLISH)
        _add_chunk(collection, file, "Paris is the capital of France.", _unit_vector(0))

        with mock.patch.object(type(collection), "get_query_vector", return_value=_unit_vector(0)):
            with override_flag(HYBRID_FLAG, active=False):
                results = search_collection(collection, "capital of France", top_k=5)

        assert len(results) == 1
        assert all(result.distance is not None for result in results)

    def test_reranked_results_carry_a_rerank_score(self):
        collection, file = _make_indexed_collection()
        _add_chunk(collection, file, "some prose", _unit_vector(0))
        _add_chunk(collection, file, "other prose", _unit_vector(1))

        results = _search_with_reranker(
            collection, "prose", _StubReranker([RerankedDocument(0, 0.8), RerankedDocument(1, 0.2)]), top_k=2
        )

        assert len(results) == 2
        assert [result.rerank_score for result in results] == [0.8, 0.2]

    @pytest.mark.parametrize(
        ("score_kind", "expected", "absent"),
        [
            pytest.param("rerank", "Relevance:", "Distance:", id="rerank-renders-relevance"),
            pytest.param("hybrid", "Score:", "Distance:", id="hybrid-renders-score"),
            pytest.param("dense", "Distance:", "Score:", id="dense-renders-distance"),
        ],
    )
    def test_template_renders_the_right_number(self, score_kind, expected, absent):
        collection, file = _make_indexed_collection()
        chunk = _add_chunk(collection, file, "some prose", _unit_vector(0))
        chunk.distance = 0.25
        chunk.fused_score = 0.0123
        chunk.rerank_score = 0.4567

        html = render_to_string(
            "documents/collection_query_results.html", {"chunks": [chunk], "score_kind": score_kind}
        )

        assert expected in html
        assert absent not in html

    @pytest.mark.parametrize(
        ("score_kind", "attribute", "expected"),
        [
            pytest.param("hybrid", "fused_score", "Score:", id="zero-fused-score"),
            pytest.param("rerank", "rerank_score", "Relevance:", id="zero-rerank-score"),
        ],
    )
    def test_a_zero_score_still_renders_as_a_score(self, score_kind, attribute, expected):
        """Zero is a legitimate score on both branches: a fused score of 0 happens when the dense
        weight is 0 and a chunk was found by dense search alone, and a reranker is free to score a
        candidate 0. Testing the value for truthiness would send either down the distance branch,
        which has no distance to render.
        """
        collection, file = _make_indexed_collection()
        chunk = _add_chunk(collection, file, "some prose", _unit_vector(0))
        setattr(chunk, attribute, 0.0)

        html = render_to_string(
            "documents/collection_query_results.html", {"chunks": [chunk], "score_kind": score_kind}
        )

        assert expected in html
        assert "Distance:" not in html

    @pytest.mark.parametrize(
        ("annotations", "expected"),
        [
            pytest.param({"rerank_score": 0.0, "fused_score": 0.5, "distance": 0.1}, "rerank", id="rerank-wins"),
            pytest.param({"fused_score": 0.0, "distance": 0.1}, "hybrid", id="fused-beats-distance"),
            pytest.param({"distance": 0.1}, "dense", id="distance-only"),
        ],
    )
    def test_score_kind_prefers_the_latest_stage_that_ran(self, annotations, expected):
        """Reranked results still carry whatever the stage beneath them annotated -- a dense-only
        rerank keeps its `distance` -- so the order these are checked in is what decides.
        """
        collection, file = _make_indexed_collection()
        chunk = _add_chunk(collection, file, "some prose", _unit_vector(0))
        for attribute, value in annotations.items():
            setattr(chunk, attribute, value)

        assert _result_score_kind([chunk]) == expected

    def test_score_kind_of_no_results_is_empty(self):
        assert _result_score_kind([]) == ""


@pytest.mark.django_db()
class TestSearchKnobConstraints:
    """The knobs are absent from every form, so `full_clean()` never runs and the field
    validators never fire. These constraints are the only thing actually stopping a bad value,
    so they are asserted against the database rather than through a form.
    """

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            pytest.param("search_dense_weight", -0.1, id="weight-below-zero"),
            pytest.param("search_dense_weight", 1.1, id="weight-above-one"),
            pytest.param("search_dense_weight", None, id="weight-null"),
            pytest.param("search_fetch_k", 0, id="fetch-k-zero"),
            pytest.param("search_fetch_k", None, id="fetch-k-null"),
            pytest.param("rerank_top_n", 0, id="rerank-top-n-zero"),
            pytest.param("rerank_top_n", None, id="rerank-top-n-null"),
        ],
    )
    def test_out_of_range_values_are_rejected(self, field, value):
        collection = CollectionFactory.create()
        setattr(collection, field, value)
        # `save()` deliberately skips `full_clean()`, so this reaches the database the same way
        # application code would. The write is wrapped in a nested atomic() so the aborted
        # transaction rolls back to a savepoint and the surrounding test transaction stays usable.
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                collection.save()

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            pytest.param("search_dense_weight", 0.0, id="weight-zero"),
            pytest.param("search_dense_weight", 1.0, id="weight-one"),
            pytest.param("search_fetch_k", 1, id="fetch-k-one"),
            pytest.param("rerank_top_n", 1, id="rerank-top-n-one"),
        ],
    )
    def test_valid_boundary_values_are_accepted(self, field, value):
        collection = CollectionFactory.create()
        setattr(collection, field, value)
        collection.save()
        collection.refresh_from_db()
        assert getattr(collection, field) == value


class _StubReranker(Reranker):
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


def _search_with_reranker(collection, query, reranker, *, top_k=5, context=None, hybrid=False):
    """Run `search_collection` with `reranker` as the collection's reranker.

    `Collection.get_reranker` is stubbed rather than the provider SDK so these tests exercise the
    rerank stage, not the Voyage adapter (which has its own tests). The gating that decides
    whether a reranker is built at all is tested separately, against the real method.
    """
    with mock.patch.object(type(collection), "get_query_vector", return_value=_unit_vector(0)):
        with mock.patch.object(type(collection), "get_reranker", return_value=reranker):
            with override_flag(HYBRID_FLAG, active=hybrid):
                return search_collection(collection, query, top_k=top_k, context=context)


@pytest.mark.django_db()
class TestRerankStage:
    """The rerank stage reorders whatever candidates retrieval produced and keeps the best few.

    Its contract is that it can only improve the ranking: every way it can go wrong has to leave
    the search with the ranking it already had.
    """

    def test_reorders_the_candidates(self):
        collection, file = _make_indexed_collection()
        first = _add_chunk(collection, file, "alpha", _unit_vector(0))
        second = _add_chunk(collection, file, "beta", _unit_vector(1))

        # Dense search ranks `first` above `second` (the query vector is `first`'s); the reranker
        # disagrees, and the reranker is the last word.
        reranker = _StubReranker([RerankedDocument(1, 0.9), RerankedDocument(0, 0.1)])
        results = _search_with_reranker(collection, "alpha", reranker, top_k=2)

        assert [result.id for result in results] == [second.id, first.id]
        assert [result.rerank_score for result in results] == [0.9, 0.1]

    def test_widens_the_candidate_pool_to_rerank_top_n(self):
        """The stage's whole value is seeing more candidates than the caller asked for. Without
        the widening it would only ever reshuffle the `top_k` dense search already picked.
        """
        collection, file = _make_indexed_collection(rerank_top_n=3)
        for index in range(3):
            _add_chunk(collection, file, f"chunk {index}", _unit_vector(index))

        reranker = _StubReranker([RerankedDocument(2, 0.9)])
        results = _search_with_reranker(collection, "chunk", reranker, top_k=1)

        query, documents, limit = reranker.calls[0]
        assert len(documents) == 3
        assert limit == 1
        assert len(results) == 1

    def test_rerank_top_n_never_narrows_below_top_k(self):
        """A collection configured with a pool smaller than the caller's `top_k` must not cost the
        caller results."""
        collection, file = _make_indexed_collection(rerank_top_n=1)
        for index in range(3):
            _add_chunk(collection, file, f"chunk {index}", _unit_vector(index))

        reranker = _StubReranker([RerankedDocument(index, 1.0 - index / 10) for index in range(3)])
        results = _search_with_reranker(collection, "chunk", reranker, top_k=3)

        assert len(reranker.calls[0][1]) == 3
        assert len(results) == 3

    def test_scores_the_contextualized_text(self):
        """Contextual retrieval's header is part of what was embedded and indexed, so it is part
        of what the reranker has to see -- otherwise it scores a chunk the index does not have.
        """
        collection, file = _make_indexed_collection()
        _add_chunk(collection, file, "It grew 3% over the quarter.", _unit_vector(0), context="Acme Q3 results.")
        _add_chunk(collection, file, "unrelated", _unit_vector(1))

        reranker = _StubReranker([RerankedDocument(0, 1.0)])
        _search_with_reranker(collection, "Acme growth", reranker, top_k=1)

        assert reranker.calls[0][1][0] == "Acme Q3 results.\n\nIt grew 3% over the quarter."

    def test_reading_the_context_header_does_not_cost_a_query_per_candidate(self):
        """`contextualized_text` reads `context`, which has to be in retrieval's deferred field
        set. Left deferred, every candidate would fetch its own. The claim is that the query count
        does not grow with the pool, so it is compared across pool sizes rather than pinned to a
        number that would need editing every time retrieval gains a query.
        """
        collection, file = _make_indexed_collection(rerank_top_n=10)
        reranker = _StubReranker([RerankedDocument(0, 1.0)])

        for index in range(2):
            _add_chunk(collection, file, f"chunk {index}", _unit_vector(index), context=f"header {index}")
        with CaptureQueriesContext(connection) as two_candidates:
            _search_with_reranker(collection, "chunk", reranker, top_k=1)

        for index in range(2, 8):
            _add_chunk(collection, file, f"chunk {index}", _unit_vector(index), context=f"header {index}")
        with CaptureQueriesContext(connection) as eight_candidates:
            _search_with_reranker(collection, "chunk", reranker, top_k=1)

        # Proves the pool really did grow, so the comparison below is not vacuous.
        assert len(reranker.calls[0][1]) == 2
        assert len(reranker.calls[1][1]) == 8
        assert "header 0" in reranker.calls[1][1][0]
        assert len(eight_candidates) == len(two_candidates)

    @pytest.mark.parametrize("hybrid", [True, False], ids=["fused-candidates", "dense-candidates"])
    def test_reranks_either_kind_of_candidate_list(self, hybrid):
        """The two flags are independent, so the stage has to work on a fused ranking and on a
        dense-only one."""
        collection, file = _make_indexed_collection(search_language=SearchLanguage.ENGLISH)
        first = _add_chunk(collection, file, "Paris is the capital of France.", _unit_vector(0))
        second = _add_chunk(collection, file, "Lyon is a city in France.", _unit_vector(1))

        reranker = _StubReranker([RerankedDocument(1, 0.9), RerankedDocument(0, 0.1)])
        results = _search_with_reranker(collection, "capital of France", reranker, top_k=2, hybrid=hybrid)

        assert {result.id for result in results} == {first.id, second.id}
        assert results[0].rerank_score == 0.9

    def test_returns_only_what_the_reranker_ranked(self):
        """ "Fewer candidates than asked for" is a legitimate answer; the stage must not pad it
        back out with candidates the reranker rejected."""
        collection, file = _make_indexed_collection()
        for index in range(3):
            _add_chunk(collection, file, f"chunk {index}", _unit_vector(index))

        reranker = _StubReranker([RerankedDocument(1, 0.9)])
        results = _search_with_reranker(collection, "chunk", reranker, top_k=3)

        assert len(results) == 1


@pytest.mark.django_db()
class TestRerankStageFallbacks:
    """Reranking is a quality stage layered on a ranking that already works. Every failure has to
    cost the search its improvement, never its results."""

    @pytest.mark.parametrize(
        "reranker",
        [
            pytest.param(_StubReranker(error=RuntimeError("provider is down")), id="provider-error"),
            pytest.param(_StubReranker([RerankedDocument(9, 0.9)]), id="out-of-range-index"),
            pytest.param(_StubReranker([RerankedDocument(0, 0.9), RerankedDocument(0, 0.1)]), id="duplicate-index"),
            pytest.param(_StubReranker([]), id="empty-ranking"),
        ],
    )
    def test_falls_back_to_the_pre_rerank_order(self, reranker):
        collection, file = _make_indexed_collection()
        near = _add_chunk(collection, file, "alpha", _unit_vector(0))
        far = _add_chunk(collection, file, "beta", _unit_vector(1))

        results = _search_with_reranker(collection, "alpha", reranker, top_k=2)

        # The dense ordering, unchanged, and with no rerank score to advertise otherwise.
        assert [result.id for result in results] == [near.id, far.id]
        assert all(getattr(result, "rerank_score", None) is None for result in results)

    def test_a_failed_rerank_still_honours_top_k(self):
        """The candidate pool is wider than `top_k`. Falling back must not hand the caller the
        whole pool."""
        collection, file = _make_indexed_collection(rerank_top_n=5)
        for index in range(5):
            _add_chunk(collection, file, f"chunk {index}", _unit_vector(index))

        reranker = _StubReranker(error=RuntimeError("provider is down"))
        results = _search_with_reranker(collection, "chunk", reranker, top_k=2)

        assert len(results) == 2

    def test_a_request_for_no_results_is_not_sent_to_the_provider(self):
        """`top_k` of 0 is what makes an empty ranking legitimate. Answering it here keeps the
        malformed-answer check below unambiguous: past this point, no results means no results
        the reranker could name.
        """
        collection, file = _make_indexed_collection()
        for index in range(2):
            _add_chunk(collection, file, f"chunk {index}", _unit_vector(index))

        reranker = _StubReranker([RerankedDocument(0, 0.9)])
        assert _search_with_reranker(collection, "chunk", reranker, top_k=0) == []
        assert reranker.calls == []

    def test_a_lone_candidate_is_not_sent_to_the_provider(self):
        """One candidate cannot be reordered, and the call would still be billed."""
        collection, file = _make_indexed_collection()
        only = _add_chunk(collection, file, "alpha", _unit_vector(0))

        reranker = _StubReranker([RerankedDocument(0, 0.9)])
        results = _search_with_reranker(collection, "alpha", reranker, top_k=5)

        assert reranker.calls == []
        assert [result.id for result in results] == [only.id]

    @pytest.mark.parametrize("query", ["", "   ", "\n"], ids=["empty", "spaces", "newline"])
    def test_a_blank_query_is_not_sent_to_the_provider(self, query):
        """Dense search still ranks a blank query by whatever its embedding came out as. A
        reranker has nothing to score against, so the stage is skipped rather than paid for."""
        collection, file = _make_indexed_collection()
        for index in range(2):
            _add_chunk(collection, file, f"chunk {index}", _unit_vector(index))

        reranker = _StubReranker([RerankedDocument(1, 0.9)])
        results = _search_with_reranker(collection, query, reranker, top_k=2)

        assert reranker.calls == []
        assert len(results) == 2


@pytest.mark.django_db()
class TestRerankContextConditioning:
    """The `context` parameter is issue #2681's context conditioning: recent conversation turns,
    used to tell the reranker which question is actually being asked."""

    def test_context_is_prepended_to_the_query(self):
        collection, file = _make_indexed_collection()
        for index in range(2):
            _add_chunk(collection, file, f"chunk {index}", _unit_vector(index))

        reranker = _StubReranker([RerankedDocument(0, 0.9)])
        _search_with_reranker(collection, "how much does it cost", reranker, context="user: tell me about the permit")

        assert reranker.calls[0][0] == "user: tell me about the permit\n\nhow much does it cost"

    @pytest.mark.parametrize("context", [None, "", "   "], ids=["none", "empty", "whitespace"])
    def test_no_context_leaves_the_query_alone(self, context):
        collection, file = _make_indexed_collection()
        for index in range(2):
            _add_chunk(collection, file, f"chunk {index}", _unit_vector(index))

        reranker = _StubReranker([RerankedDocument(0, 0.9)])
        _search_with_reranker(collection, "how much does it cost", reranker, context=context)

        assert reranker.calls[0][0] == "how much does it cost"

    def test_a_long_context_is_clipped_but_the_query_is_not(self):
        """The provider clips the pair to the model's context window from the end. An unbounded
        context would therefore push the query itself out of the window -- the one part the
        reranker has to see in full.
        """
        collection, file = _make_indexed_collection()
        for index in range(2):
            _add_chunk(collection, file, f"chunk {index}", _unit_vector(index))

        query = "how much does it cost"
        reranker = _StubReranker([RerankedDocument(0, 0.9)])
        _search_with_reranker(collection, query, reranker, context="x" * (MAX_RERANK_CONTEXT_CHARS * 3))

        sent = reranker.calls[0][0]
        assert sent.endswith(f"\n\n{query}")
        assert len(sent) == MAX_RERANK_CONTEXT_CHARS + 2 + len(query)

    def test_the_tail_of_the_context_is_what_survives(self):
        """The recent turns are the ones that disambiguate a follow-up question, so clipping takes
        the end, not the beginning."""
        collection, file = _make_indexed_collection()
        for index in range(2):
            _add_chunk(collection, file, f"chunk {index}", _unit_vector(index))

        context = "oldest turn " + "-" * MAX_RERANK_CONTEXT_CHARS + " newest turn"
        reranker = _StubReranker([RerankedDocument(0, 0.9)])
        _search_with_reranker(collection, "query", reranker, context=context)

        sent = reranker.calls[0][0]
        assert "newest turn" in sent
        assert "oldest turn" not in sent

    def test_context_is_ignored_when_there_is_no_reranker(self):
        """Nothing else reads it, so a caller passing context to a collection without reranking
        must get exactly the ranking it would have got anyway."""
        collection, file = _make_indexed_collection()
        near = _add_chunk(collection, file, "alpha", _unit_vector(0))
        far = _add_chunk(collection, file, "beta", _unit_vector(1))

        with mock.patch.object(type(collection), "get_query_vector", return_value=_unit_vector(0)):
            with override_flag(HYBRID_FLAG, active=False):
                with_context = search_collection(collection, "alpha", top_k=2, context="something")
                without_context = search_collection(collection, "alpha", top_k=2)

        assert [chunk.id for chunk in with_context] == [chunk.id for chunk in without_context] == [near.id, far.id]


def _rerankable_collection(**kwargs):
    """A collection configured so that `get_reranker` has everything it needs."""
    collection, file = _make_indexed_collection(enable_reranking=True, **kwargs)
    collection.reranker_provider = LlmProviderFactory.create(
        team=collection.team,
        type=str(LlmProviderTypes.voyage),
        config={"voyage_api_key": "test-voyage-key"},
    )
    collection.save()
    return collection, file


@pytest.mark.django_db()
class TestRerankerGating:
    """`Collection.get_reranker` decides whether the stage runs at all. It returns None for every
    reason not to, because a misconfigured optional stage must not fail a search that works.
    """

    def test_builds_a_reranker_for_the_configured_provider_and_model(self):
        collection, _ = _rerankable_collection(rerank_model="rerank-2-lite")

        with override_flag(RERANK_FLAG, active=True):
            reranker = collection.get_reranker()

        assert isinstance(reranker, VoyageReranker)
        assert reranker._model == "rerank-2-lite"

    def test_flag_off_builds_nothing(self):
        """The regression guarantee: a collection can be fully configured for reranking and still
        behave exactly as it did before, until the flag is turned on for its team."""
        collection, _ = _rerankable_collection()

        with override_flag(RERANK_FLAG, active=False):
            assert collection.get_reranker() is None
            assert collection.reranking_enabled is False

    def test_disabled_on_the_collection_builds_nothing(self):
        collection, _ = _rerankable_collection()
        collection.enable_reranking = False

        with override_flag(RERANK_FLAG, active=True):
            assert collection.get_reranker() is None

    def test_remote_indexes_build_nothing(self):
        """A remote index's chunks live at the provider and never reach a ranking stage OCS
        controls, exactly as for hybrid search."""
        collection, _ = _rerankable_collection()
        collection.is_remote_index = True

        with override_flag(RERANK_FLAG, active=True):
            assert collection.get_reranker() is None

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            pytest.param("reranker_provider", None, id="no-provider"),
            pytest.param("rerank_model", "", id="no-model"),
        ],
    )
    def test_incomplete_configuration_builds_nothing(self, field, value):
        collection, _ = _rerankable_collection()
        setattr(collection, field, value)

        with override_flag(RERANK_FLAG, active=True):
            assert collection.get_reranker() is None

    def test_a_provider_without_a_rerank_endpoint_builds_nothing(self):
        """Only Voyage offers one. Every other provider's service raises NotImplementedError,
        which has to read as "skip the stage", not as a failed search."""
        collection, _ = _rerankable_collection()
        collection.reranker_provider = LlmProviderFactory.create(team=collection.team)

        with override_flag(RERANK_FLAG, active=True):
            assert collection.get_reranker() is None


@pytest.mark.django_db()
class TestRerankingThroughTheProvider:
    """One test of the whole chain -- collection configuration, provider credentials, the Voyage
    adapter, the rerank stage -- with only the network faked, so the wiring is proven end to end
    rather than inferred from the stubbed tests above.
    """

    def test_a_configured_collection_returns_the_providers_ranking(self):
        collection, file = _rerankable_collection()
        first = _add_chunk(collection, file, "alpha", _unit_vector(0))
        second = _add_chunk(collection, file, "beta", _unit_vector(1))

        with mock.patch("voyageai.Client") as client_cls:
            client_cls.return_value.rerank.return_value = SimpleNamespace(
                results=[
                    SimpleNamespace(index=1, relevance_score=0.91),
                    SimpleNamespace(index=0, relevance_score=0.12),
                ]
            )
            with mock.patch.object(type(collection), "get_query_vector", return_value=_unit_vector(0)):
                with override_flag(RERANK_FLAG, active=True):
                    results = search_collection(collection, "alpha", top_k=2)

        # Dense search puts `first` on top; the provider's ranking overrides it.
        assert [result.id for result in results] == [second.id, first.id]
        assert [result.rerank_score for result in results] == [0.91, 0.12]
        assert client_cls.call_args.kwargs["api_key"] == "test-voyage-key"
        assert client_cls.return_value.rerank.call_args.kwargs["model"] == "rerank-2"

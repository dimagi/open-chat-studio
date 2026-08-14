from unittest import mock

import pytest
from django.conf import settings
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from waffle.testutils import override_flag

from apps.documents.models import CollectionFile, FileStatus, SearchLanguage
from apps.documents.retrieval import _lexical_candidate_ids, _rank_by_score, _rrf_scores, search_collection
from apps.service_providers.llm_service.index_managers import LocalIndexManager
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.files import FileChunkEmbeddingFactory, FileFactory

HYBRID_FLAG = "flag_hybrid_search"


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

    def test_language_change_without_a_rebuild_finds_nothing_until_rebuilt(self):
        """The drift trap: chunks keep the configuration they were indexed with.

        A collection indexed as Spanish but queried as English returns no lexical hits, which is
        indistinguishable from a query that simply has none. `rebuild_search_vectors` is the
        supported way back.
        """
        collection, file = _make_indexed_collection(search_language=SearchLanguage.SPANISH)
        chunk = _add_chunk(collection, file, "Paris es la capital de Francia.", _unit_vector(0))

        collection.search_language = SearchLanguage.ENGLISH
        collection.save()
        assert _lexical_candidate_ids(collection, "capital of Francia", 10) == []

        assert collection.rebuild_search_vectors() == 1
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

        assert [result.fused_score for result in results] == [pytest.approx(result.fused_score) for result in results]
        assert all(result.fused_score is not None and result.fused_score > 0 for result in results)

    def test_dense_only_results_keep_their_distance(self):
        collection, file = _make_indexed_collection(search_language=SearchLanguage.ENGLISH)
        _add_chunk(collection, file, "Paris is the capital of France.", _unit_vector(0))

        with mock.patch.object(type(collection), "get_query_vector", return_value=_unit_vector(0)):
            with override_flag(HYBRID_FLAG, active=False):
                results = search_collection(collection, "capital of France", top_k=5)

        assert all(result.distance is not None for result in results)

    @pytest.mark.parametrize(
        ("context", "expected", "absent"),
        [
            pytest.param({"hybrid_search": True}, "Score:", "Distance:", id="hybrid-renders-score"),
            pytest.param({"hybrid_search": False}, "Distance:", "Score:", id="dense-renders-distance"),
        ],
    )
    def test_template_renders_the_right_number(self, context, expected, absent):
        collection, file = _make_indexed_collection()
        chunk = _add_chunk(collection, file, "some prose", _unit_vector(0))
        chunk.distance = 0.25
        chunk.fused_score = 0.0123 if context["hybrid_search"] else None

        html = render_to_string("documents/collection_query_results.html", {"chunks": [chunk], **context})

        assert expected in html
        assert absent not in html


@pytest.mark.django_db()
class TestHybridSearchKnobConstraints:
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
        ],
    )
    def test_valid_boundary_values_are_accepted(self, field, value):
        collection = CollectionFactory.create()
        setattr(collection, field, value)
        collection.save()
        collection.refresh_from_db()
        assert getattr(collection, field) == value

"""The rerank stage: reordering retrieval candidates by scoring them against the query.

The Voyage adapter itself is tested in `test_rerankers.py`; these tests stub the reranker so the
stage's own behaviour is what is under test, with one end-to-end case through the real adapter.
"""

import logging
from unittest import mock

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from waffle.testutils import override_flag

from apps.documents.models import Collection, SearchLanguage
from apps.documents.rerankers import RerankedDocument, VoyageReranker
from apps.documents.retrieval import MAX_RERANK_CONTEXT_CHARS, search_collection
from apps.documents.tests.retrieval_helpers import (
    HYBRID_FLAG,
    RERANK_FLAG,
    StubReranker,
    add_chunk,
    make_indexed_collection,
    rerankable_collection,
    search_with_reranker,
    unit_vector,
    voyage_response,
)
from apps.service_providers.models import LlmProviderTypes
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.factories.team import TeamFactory


@pytest.mark.django_db()
class TestRerankStage:
    """The rerank stage reorders whatever candidates retrieval produced and keeps the best few.

    Its contract is that it can only improve the ranking: every way it can go wrong has to leave
    the search with the ranking it already had.
    """

    def test_reorders_the_candidates(self):
        collection, file = make_indexed_collection()
        first = add_chunk(collection, file, "alpha", unit_vector(0))
        second = add_chunk(collection, file, "beta", unit_vector(1))

        # Dense search ranks `first` above `second` (the query vector is `first`'s); the reranker
        # disagrees, and the reranker is the last word.
        reranker = StubReranker([RerankedDocument(1, 0.9), RerankedDocument(0, 0.1)])
        results = search_with_reranker(collection, "alpha", reranker, top_k=2)

        assert [result.id for result in results] == [second.id, first.id]
        assert [result.rerank_score for result in results] == [0.9, 0.1]

    def test_widens_the_candidate_pool_to_rerank_top_n(self):
        """The stage's whole value is seeing more candidates than the caller asked for. Without
        the widening it would only ever reshuffle the `top_k` dense search already picked.
        """
        collection, file = make_indexed_collection(rerank_top_n=3)
        for index in range(3):
            add_chunk(collection, file, f"chunk {index}", unit_vector(index))

        reranker = StubReranker([RerankedDocument(2, 0.9)])
        results = search_with_reranker(collection, "chunk", reranker, top_k=1)

        query, documents, limit = reranker.calls[0]
        assert len(documents) == 3
        assert limit == 1
        assert len(results) == 1

    def test_rerank_top_n_never_narrows_below_top_k(self):
        """A collection configured with a pool smaller than the caller's `top_k` must not cost the
        caller results."""
        collection, file = make_indexed_collection(rerank_top_n=1)
        for index in range(3):
            add_chunk(collection, file, f"chunk {index}", unit_vector(index))

        reranker = StubReranker([RerankedDocument(index, 1.0 - index / 10) for index in range(3)])
        results = search_with_reranker(collection, "chunk", reranker, top_k=3)

        assert len(reranker.calls[0][1]) == 3
        assert len(results) == 3

    def test_scores_the_contextualized_text(self):
        """Contextual retrieval's header is part of what was embedded and indexed, so it is part
        of what the reranker has to see -- otherwise it scores a chunk the index does not have.
        """
        collection, file = make_indexed_collection()
        add_chunk(collection, file, "It grew 3% over the quarter.", unit_vector(0), context="Acme Q3 results.")
        add_chunk(collection, file, "unrelated", unit_vector(1))

        reranker = StubReranker([RerankedDocument(0, 1.0)])
        search_with_reranker(collection, "Acme growth", reranker, top_k=1)

        assert reranker.calls[0][1][0] == "Acme Q3 results.\n\nIt grew 3% over the quarter."

    def test_reading_the_context_header_does_not_cost_a_query_per_candidate(self):
        """`contextualized_text` reads `context`, which has to be in retrieval's deferred field
        set. Left deferred, every candidate would fetch its own. The claim is that the query count
        does not grow with the pool, so it is compared across pool sizes rather than pinned to a
        number that would need editing every time retrieval gains a query.
        """
        collection, file = make_indexed_collection(rerank_top_n=10)
        reranker = StubReranker([RerankedDocument(0, 1.0)])

        for index in range(2):
            add_chunk(collection, file, f"chunk {index}", unit_vector(index), context=f"header {index}")
        with CaptureQueriesContext(connection) as two_candidates:
            search_with_reranker(collection, "chunk", reranker, top_k=1)

        for index in range(2, 8):
            add_chunk(collection, file, f"chunk {index}", unit_vector(index), context=f"header {index}")
        with CaptureQueriesContext(connection) as eight_candidates:
            search_with_reranker(collection, "chunk", reranker, top_k=1)

        # Proves the pool really did grow, so the comparison below is not vacuous.
        assert len(reranker.calls[0][1]) == 2
        assert len(reranker.calls[1][1]) == 8
        assert "header 0" in reranker.calls[1][1][0]
        assert len(eight_candidates) == len(two_candidates)

    @pytest.mark.parametrize("hybrid", [True, False], ids=["fused-candidates", "dense-candidates"])
    def test_reranks_either_kind_of_candidate_list(self, hybrid):
        """The two flags are independent, so the stage has to work on a fused ranking and on a
        dense-only one."""
        collection, file = make_indexed_collection(search_language=SearchLanguage.ENGLISH)
        first = add_chunk(collection, file, "Paris is the capital of France.", unit_vector(0))
        second = add_chunk(collection, file, "Lyon is a city in France.", unit_vector(1))

        reranker = StubReranker([RerankedDocument(1, 0.9), RerankedDocument(0, 0.1)])
        results = search_with_reranker(collection, "capital of France", reranker, top_k=2, hybrid=hybrid)

        assert {result.id for result in results} == {first.id, second.id}
        assert results[0].rerank_score == 0.9

    def test_returns_only_what_the_reranker_ranked(self):
        """ "Fewer candidates than asked for" is a legitimate answer; the stage must not pad it
        back out with candidates the reranker rejected."""
        collection, file = make_indexed_collection()
        for index in range(3):
            add_chunk(collection, file, f"chunk {index}", unit_vector(index))

        reranker = StubReranker([RerankedDocument(1, 0.9)])
        results = search_with_reranker(collection, "chunk", reranker, top_k=3)

        assert len(results) == 1


@pytest.mark.django_db()
class TestRerankStageFallbacks:
    """Reranking is a quality stage layered on a ranking that already works. Every failure has to
    cost the search its improvement, never its results."""

    @pytest.mark.parametrize(
        "reranker",
        [
            pytest.param(StubReranker(error=RuntimeError("provider is down")), id="provider-error"),
            pytest.param(StubReranker([RerankedDocument(9, 0.9)]), id="out-of-range-index"),
            pytest.param(StubReranker([RerankedDocument(0, 0.9), RerankedDocument(0, 0.1)]), id="duplicate-index"),
            pytest.param(StubReranker([]), id="empty-ranking"),
        ],
    )
    def test_falls_back_to_the_pre_rerank_order(self, reranker):
        collection, file = make_indexed_collection()
        near = add_chunk(collection, file, "alpha", unit_vector(0))
        far = add_chunk(collection, file, "beta", unit_vector(1))

        results = search_with_reranker(collection, "alpha", reranker, top_k=2)

        # The dense ordering, unchanged, and with no rerank score to advertise otherwise.
        assert [result.id for result in results] == [near.id, far.id]
        assert all(getattr(result, "rerank_score", None) is None for result in results)

    def test_a_failed_rerank_still_honours_top_k(self):
        """The candidate pool is wider than `top_k`. Falling back must not hand the caller the
        whole pool."""
        collection, file = make_indexed_collection(rerank_top_n=5)
        for index in range(5):
            add_chunk(collection, file, f"chunk {index}", unit_vector(index))

        reranker = StubReranker(error=RuntimeError("provider is down"))
        results = search_with_reranker(collection, "chunk", reranker, top_k=2)

        assert len(results) == 2

    def test_a_request_for_no_results_is_not_sent_to_the_provider(self):
        """`top_k` of 0 is what makes an empty ranking legitimate. Answering it here keeps the
        malformed-answer check below unambiguous: past this point, no results means no results
        the reranker could name.
        """
        collection, file = make_indexed_collection()
        for index in range(2):
            add_chunk(collection, file, f"chunk {index}", unit_vector(index))

        reranker = StubReranker([RerankedDocument(0, 0.9)])
        assert search_with_reranker(collection, "chunk", reranker, top_k=0) == []
        assert reranker.calls == []

    def test_a_lone_candidate_is_not_sent_to_the_provider(self):
        """One candidate cannot be reordered, and the call would still be billed."""
        collection, file = make_indexed_collection()
        only = add_chunk(collection, file, "alpha", unit_vector(0))

        reranker = StubReranker([RerankedDocument(0, 0.9)])
        results = search_with_reranker(collection, "alpha", reranker, top_k=5)

        assert reranker.calls == []
        assert [result.id for result in results] == [only.id]

    @pytest.mark.parametrize("query", ["", "   ", "\n"], ids=["empty", "spaces", "newline"])
    def test_a_blank_query_is_not_sent_to_the_provider(self, query):
        """Dense search still ranks a blank query by whatever its embedding came out as. A
        reranker has nothing to score against, so the stage is skipped rather than paid for."""
        collection, file = make_indexed_collection()
        for index in range(2):
            add_chunk(collection, file, f"chunk {index}", unit_vector(index))

        reranker = StubReranker([RerankedDocument(1, 0.9)])
        results = search_with_reranker(collection, query, reranker, top_k=2)

        assert reranker.calls == []
        assert len(results) == 2


@pytest.mark.django_db()
class TestRerankContextConditioning:
    """The `context` parameter is issue #2681's context conditioning: recent conversation turns,
    used to tell the reranker which question is actually being asked."""

    def test_context_is_prepended_to_the_query(self):
        collection, file = make_indexed_collection()
        for index in range(2):
            add_chunk(collection, file, f"chunk {index}", unit_vector(index))

        reranker = StubReranker([RerankedDocument(0, 0.9)])
        search_with_reranker(collection, "how much does it cost", reranker, context="user: tell me about the permit")

        assert reranker.calls[0][0] == "user: tell me about the permit\n\nhow much does it cost"

    @pytest.mark.parametrize("context", [None, "", "   "], ids=["none", "empty", "whitespace"])
    def test_no_context_leaves_the_query_alone(self, context):
        collection, file = make_indexed_collection()
        for index in range(2):
            add_chunk(collection, file, f"chunk {index}", unit_vector(index))

        reranker = StubReranker([RerankedDocument(0, 0.9)])
        search_with_reranker(collection, "how much does it cost", reranker, context=context)

        assert reranker.calls[0][0] == "how much does it cost"

    def test_a_long_context_is_clipped_but_the_query_is_not(self):
        """The provider clips the pair to the model's context window from the end. An unbounded
        context would therefore push the query itself out of the window -- the one part the
        reranker has to see in full.
        """
        collection, file = make_indexed_collection()
        for index in range(2):
            add_chunk(collection, file, f"chunk {index}", unit_vector(index))

        query = "how much does it cost"
        reranker = StubReranker([RerankedDocument(0, 0.9)])
        search_with_reranker(collection, query, reranker, context="x" * (MAX_RERANK_CONTEXT_CHARS * 3))

        sent = reranker.calls[0][0]
        assert sent.endswith(f"\n\n{query}")
        assert len(sent) == MAX_RERANK_CONTEXT_CHARS + 2 + len(query)

    def test_the_tail_of_the_context_is_what_survives(self):
        """The recent turns are the ones that disambiguate a follow-up question, so clipping takes
        the end, not the beginning."""
        collection, file = make_indexed_collection()
        for index in range(2):
            add_chunk(collection, file, f"chunk {index}", unit_vector(index))

        context = "oldest turn " + "-" * MAX_RERANK_CONTEXT_CHARS + " newest turn"
        reranker = StubReranker([RerankedDocument(0, 0.9)])
        search_with_reranker(collection, "query", reranker, context=context)

        sent = reranker.calls[0][0]
        assert "newest turn" in sent
        assert "oldest turn" not in sent

    def test_context_is_ignored_when_there_is_no_reranker(self):
        """Nothing else reads it, so a caller passing context to a collection without reranking
        must get exactly the ranking it would have got anyway."""
        collection, file = make_indexed_collection()
        near = add_chunk(collection, file, "alpha", unit_vector(0))
        far = add_chunk(collection, file, "beta", unit_vector(1))

        with mock.patch.object(type(collection), "get_query_vector", return_value=unit_vector(0)):
            with override_flag(HYBRID_FLAG, active=False):
                with_context = search_collection(collection, "alpha", top_k=2, context="something")
                without_context = search_collection(collection, "alpha", top_k=2)

        assert [chunk.id for chunk in with_context] == [chunk.id for chunk in without_context] == [near.id, far.id]


@pytest.mark.django_db()
class TestRerankerGating:
    """`Collection.get_reranker` decides whether the stage runs at all. It returns None for every
    reason not to, because a misconfigured optional stage must not fail a search that works.
    """

    def test_builds_a_reranker_for_the_configured_provider_and_model(self):
        collection, _ = rerankable_collection(rerank_model="rerank-2-lite")

        with override_flag(RERANK_FLAG, active=True):
            reranker = collection.get_reranker()

        assert isinstance(reranker, VoyageReranker)
        assert reranker._model == "rerank-2-lite"

    def test_flag_off_builds_nothing(self):
        """The regression guarantee: a collection can be fully configured for reranking and still
        behave exactly as it did before, until the flag is turned on for its team."""
        collection, _ = rerankable_collection()

        with override_flag(RERANK_FLAG, active=False):
            assert collection.get_reranker() is None
            assert collection.reranking_enabled is False

    def test_disabled_on_the_collection_builds_nothing(self):
        collection, _ = rerankable_collection()
        collection.enable_reranking = False

        with override_flag(RERANK_FLAG, active=True):
            assert collection.get_reranker() is None

    def test_remote_indexes_build_nothing(self):
        """A remote index's chunks live at the provider and never reach a ranking stage OCS
        controls, exactly as for hybrid search."""
        collection, _ = rerankable_collection()
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
    def test_incomplete_configuration_reads_as_off(self, field, value):
        """Not as an error: the stage cannot run without both, and callers gate on
        `reranking_enabled` to decide whether the work leading up to it is worth doing.
        """
        collection, _ = rerankable_collection()
        setattr(collection, field, value)

        with override_flag(RERANK_FLAG, active=True):
            assert collection.reranking_enabled is False
            assert collection.get_reranker() is None

    def test_deciding_reads_no_provider_row(self):
        """`reranking_enabled` is asked on every search, and the chat search tools ask it before
        collecting conversation context, so it must answer without a provider fetch:
        `reranker_provider` would load the row, `reranker_provider_id` is already there.

        The collection is re-loaded first because assigning the FK caches the object on the
        instance, which would hide the fetch. A freshly loaded one is also what the callers have.
        """
        collection, _ = rerankable_collection()
        collection = Collection.objects.get(id=collection.id)

        with override_flag(RERANK_FLAG, active=True):
            with CaptureQueriesContext(connection) as queries:
                assert collection.reranking_enabled is True

        # `team` is fetched for the flag lookup; the provider row must not be.
        assert not [query for query in queries.captured_queries if "llmprovider" in query["sql"].lower()]

    def test_a_provider_without_a_rerank_endpoint_builds_nothing(self, caplog):
        """Only Voyage offers one. Every other provider's service raises NotImplementedError,
        which has to read as "skip the stage", not as a failed search."""
        collection, _ = rerankable_collection()
        collection.reranker_provider = LlmProviderFactory.create(team=collection.team)

        with caplog.at_level(logging.WARNING, logger="ocs.documents"):
            with override_flag(RERANK_FLAG, active=True):
                assert collection.get_reranker() is None

        assert "no rerank endpoint" in caplog.text

    def test_a_provider_from_another_team_builds_nothing(self, caplog):
        """The field is editable in the Django admin, which offers every team's providers.
        Reranking with another team's credentials would bill them and send this collection's
        queries to their account, so the runtime refuses rather than trusting the configuration.
        """
        collection, _ = rerankable_collection()
        other_team = TeamFactory.create()
        collection.reranker_provider = LlmProviderFactory.create(
            team=other_team, type=str(LlmProviderTypes.voyage), config={"voyage_api_key": "other-team-key"}
        )

        with caplog.at_level(logging.WARNING, logger="ocs.documents"):
            with override_flag(RERANK_FLAG, active=True):
                assert collection.get_reranker() is None

        assert "belongs to another team" in caplog.text

    def test_unusable_provider_credentials_build_nothing(self, caplog):
        """A different cause from the one above, and said so: the provider does offer a rerank
        endpoint, but its stored configuration will not construct a service. An operator reading
        the log has to be able to tell which of the two they are looking at.
        """
        collection, _ = rerankable_collection()
        collection.reranker_provider = LlmProviderFactory.create(
            team=collection.team, type=str(LlmProviderTypes.voyage), config={}
        )

        with caplog.at_level(logging.WARNING, logger="ocs.documents"):
            with override_flag(RERANK_FLAG, active=True):
                assert collection.get_reranker() is None

        assert "credentials are not usable" in caplog.text
        assert "no rerank endpoint" not in caplog.text


@pytest.mark.django_db()
class TestRerankingThroughTheProvider:
    """One test of the whole chain -- collection configuration, provider credentials, the Voyage
    adapter, the rerank stage -- with only the network faked, so the wiring is proven end to end
    rather than inferred from the stubbed tests above.
    """

    def test_a_configured_collection_returns_the_providers_ranking(self):
        collection, file = rerankable_collection()
        first = add_chunk(collection, file, "alpha", unit_vector(0))
        second = add_chunk(collection, file, "beta", unit_vector(1))

        with mock.patch("voyageai.Client") as client_cls:
            client_cls.return_value.rerank.return_value = voyage_response((1, 0.91), (0, 0.12))
            with mock.patch.object(type(collection), "get_query_vector", return_value=unit_vector(0)):
                with override_flag(RERANK_FLAG, active=True):
                    results = search_collection(collection, "alpha", top_k=2)

        # Dense search puts `first` on top; the provider's ranking overrides it.
        assert [result.id for result in results] == [second.id, first.id]
        assert [result.rerank_score for result in results] == [0.91, 0.12]
        assert client_cls.call_args.kwargs["api_key"] == "test-voyage-key"
        assert client_cls.return_value.rerank.call_args.kwargs["model"] == "rerank-2"


@pytest.mark.django_db()
class TestRerankColumnDatabaseDefaults:
    """The three non-nullable rerank columns carry database-level defaults, not just Python ones.

    Django applies `default` when *it* builds the INSERT, and drops the DDL default once the
    column is backfilled. An INSERT from the release that predates the column therefore names
    none of them and hits a NOT NULL violation, which is what `db_default` prevents. Asserted
    against the database because that is the only place the guarantee actually lives.
    """

    @pytest.mark.parametrize(
        ("column", "expected"),
        [
            pytest.param("enable_reranking", "false", id="enable-reranking"),
            pytest.param("rerank_model", "'rerank-2'", id="rerank-model"),
            pytest.param("rerank_top_n", "50", id="rerank-top-n"),
        ],
    )
    def test_column_has_a_database_default(self, column, expected):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_default FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
                [Collection._meta.db_table, column],
            )
            row = cursor.fetchone()

        assert row is not None, f"{column} is missing from {Collection._meta.db_table}"
        default = row[0]
        assert default is not None, f"{column} has no database default; an old-release INSERT would fail"
        assert default.startswith(expected)

    def test_the_nullable_provider_column_needs_no_default(self):
        """`reranker_provider` is nullable, so an omitted value is already legal."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT is_nullable FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
                [Collection._meta.db_table, "reranker_provider_id"],
            )
            row = cursor.fetchone()

        assert row is not None
        assert row[0] == "YES"

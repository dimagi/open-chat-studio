from unittest import mock

import pytest

from apps.documents.rerankers import (
    VOYAGE_MAX_RETRIES,
    VOYAGE_TIMEOUT_SECONDS,
    RerankedDocument,
    VoyageReranker,
)
from apps.documents.tests.retrieval_helpers import voyage_response


class TestVoyageReranker:
    """The reranker is a thin adapter over one API call, so these tests are about the request it
    makes and the answer it hands back -- not about scoring, which happens at the provider.
    """

    def test_maps_the_response_in_the_order_the_provider_returned_it(self):
        reranker = VoyageReranker(api_key="key", model="rerank-2")
        with mock.patch("voyageai.Client") as client_cls:
            client_cls.return_value.rerank.return_value = voyage_response((2, 0.9), (0, 0.4))
            ranked = reranker.rerank("q", ["a", "b", "c"], limit=2)

        # Not sorted locally: the provider's order is the ranking, and re-sorting here would
        # silently disagree with it if two documents ever tied.
        assert ranked == [RerankedDocument(index=2, score=0.9), RerankedDocument(index=0, score=0.4)]

    def test_sends_the_configured_credentials_and_model(self):
        reranker = VoyageReranker(api_key="test-api-key", model="rerank-2-lite")
        with mock.patch("voyageai.Client") as client_cls:
            client_cls.return_value.rerank.return_value = voyage_response((0, 1.0))
            reranker.rerank("what is the capital of France", ["Paris is the capital."], limit=5)

        assert client_cls.call_args.kwargs == {
            "api_key": "test-api-key",
            "timeout": VOYAGE_TIMEOUT_SECONDS,
            "max_retries": VOYAGE_MAX_RETRIES,
        }
        request = client_cls.return_value.rerank.call_args.kwargs
        assert request["query"] == "what is the capital of France"
        assert request["documents"] == ["Paris is the capital."]
        assert request["model"] == "rerank-2-lite"
        # The document side is clipped by the provider rather than the call being rejected.
        assert request["truncation"] is True

    def test_never_asks_for_more_results_than_it_sent_documents(self):
        """`top_k` above the document count is not an error, but asking for it is meaningless and
        some providers reject it, so the request is clamped."""
        reranker = VoyageReranker(api_key="key", model="rerank-2")
        with mock.patch("voyageai.Client") as client_cls:
            client_cls.return_value.rerank.return_value = voyage_response((0, 1.0))
            reranker.rerank("q", ["only one"], limit=50)

        assert client_cls.return_value.rerank.call_args.kwargs["top_k"] == 1

    @pytest.mark.parametrize(
        ("documents", "limit"),
        [
            pytest.param([], 5, id="no-documents"),
            pytest.param(["a"], 0, id="no-results-wanted"),
            pytest.param(["a"], -1, id="negative-limit"),
        ],
    )
    def test_answers_degenerate_requests_without_calling_the_provider(self, documents, limit):
        """Voyage rejects an empty document list, and either way there is nothing to rank. A
        billed round trip to be told so would be waste."""
        reranker = VoyageReranker(api_key="key", model="rerank-2")
        with mock.patch("voyageai.Client") as client_cls:
            assert reranker.rerank("q", documents, limit=limit) == []

        client_cls.assert_not_called()

    def test_provider_failures_propagate(self):
        """The fallback policy lives in `apps.documents.retrieval`, in one place. Swallowing the
        error here would make a failed call indistinguishable from a query that ranked nothing.
        """
        reranker = VoyageReranker(api_key="key", model="rerank-2")
        with mock.patch("voyageai.Client") as client_cls:
            client_cls.return_value.rerank.side_effect = RuntimeError("429 rate limited")
            with pytest.raises(RuntimeError, match="429"):
                reranker.rerank("q", ["a", "b"], limit=2)

    def test_accepts_any_sequence_of_documents(self):
        """The retrieval caller builds the document list with a comprehension; a tuple or a
        generator-backed sequence must not change the request."""
        reranker = VoyageReranker(api_key="key", model="rerank-2")
        with mock.patch("voyageai.Client") as client_cls:
            client_cls.return_value.rerank.return_value = voyage_response((0, 1.0))
            reranker.rerank("q", ("a", "b"), limit=1)

        assert client_cls.return_value.rerank.call_args.kwargs["documents"] == ["a", "b"]

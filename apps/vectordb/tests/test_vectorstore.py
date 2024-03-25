"""Test PGVector functionality.

Ported from https://github.com/hwchase17/langchain/blob/master/tests/integration_tests/vectorstores/test_pgvector.py
"""

import pytest
from langchain.docstore.document import Document
from langchain_community.embeddings import DeterministicFakeEmbedding

from apps.utils.factories.experiment import ExperimentFactory
from apps.vectordb.const import META_ALL
from apps.vectordb.models import ADA_TOKEN_COUNT
from apps.vectordb.vectorstore import DistanceStrategy, PGVector


@pytest.fixture()
def experiment(db):
    return ExperimentFactory()


@pytest.mark.django_db()
def test_vectorstore(experiment):
    """Test end to end construction and search."""
    texts = ["foo", "bar", "baz"]
    docsearch = PGVector.from_texts(
        texts=texts,
        embedding=DeterministicFakeEmbedding(size=ADA_TOKEN_COUNT),
        experiment=experiment,
    )
    output = docsearch.similarity_search("foo", k=1)
    _remove_meta_fields(output)
    assert output == [Document(page_content="foo")]


@pytest.mark.django_db()
def test_vectorstore_with_metadatas(experiment):
    """Test end to end construction and search."""
    texts = ["foo", "bar", "baz"]
    metadatas = [{"page": str(i)} for i in range(len(texts))]
    docsearch = PGVector.from_texts(
        texts=texts,
        embedding=DeterministicFakeEmbedding(size=ADA_TOKEN_COUNT),
        metadatas=metadatas,
        experiment=experiment,
    )
    output = docsearch.similarity_search("foo", k=1)
    _remove_meta_fields(output)
    assert output == [Document(page_content="foo", metadata={"page": "0"})]


@pytest.mark.django_db()
def test_vectorstore_with_metadatas_with_scores(experiment):
    """Test end to end construction and search."""
    texts = ["foo", "bar", "baz"]
    metadatas = [{"page": str(i)} for i in range(len(texts))]
    docsearch = PGVector.from_texts(
        texts=texts,
        embedding=DeterministicFakeEmbedding(size=ADA_TOKEN_COUNT),
        metadatas=metadatas,
        experiment=experiment,
        distance_strategy=DistanceStrategy.COSINE,
    )
    output = docsearch.similarity_search_with_score("foo", k=1)
    _remove_meta_fields(output)
    assert output == [(Document(page_content="foo", metadata={"page": "0"}), 0.0)]


@pytest.mark.django_db()
def test_vectorstore_with_filter_match(experiment):
    """Test end to end construction and search."""
    texts = ["foo", "bar", "baz"]
    metadatas = [{"page": str(i)} for i in range(len(texts))]
    docsearch = PGVector.from_texts(
        texts=texts,
        embedding=DeterministicFakeEmbedding(size=ADA_TOKEN_COUNT),
        metadatas=metadatas,
        experiment=experiment,
    )
    output = docsearch.similarity_search_with_score("foo", k=1, filter={"page": "0"})
    _remove_meta_fields(output)
    assert output == [(Document(page_content="foo", metadata={"page": "0"}), 0.0)]


@pytest.mark.django_db()
def test_vectorstore_with_filter_distant_match(experiment):
    """Test end to end construction and search."""
    texts = ["foo", "bar", "baz"]
    metadatas = [{"page": str(i)} for i in range(len(texts))]
    docsearch = PGVector.from_texts(
        texts=texts,
        embedding=DeterministicFakeEmbedding(size=ADA_TOKEN_COUNT),
        metadatas=metadatas,
        experiment=experiment,
    )
    output = docsearch.similarity_search_with_score("foo", k=1, filter={"page": "2"})
    _remove_meta_fields(output)
    # ordering here is deterministic but random due to fake embeddings
    assert output == [(Document(page_content="baz", metadata={"page": "2"}), 0.9290842232061864)]


@pytest.mark.django_db()
def test_vectorstore_distant_match_cosine_ordering(experiment):
    texts = ["foo", "bar", "baz"]
    metadatas = [{"page": str(i)} for i in range(len(texts))]
    docsearch = PGVector.from_texts(
        texts=texts,
        embedding=DeterministicFakeEmbedding(size=ADA_TOKEN_COUNT),
        metadatas=metadatas,
        experiment=experiment,
        distance_strategy=DistanceStrategy.COSINE,
    )
    output = docsearch.similarity_search_with_score("foo", k=3)
    _remove_meta_fields(output)
    # ordering here is deterministic but random due to fake embeddings
    assert output == [
        (Document(page_content="foo", metadata={"page": "0"}), 0.0),
        (Document(page_content="baz", metadata={"page": "2"}), 0.9290842232061864),
        (Document(page_content="bar", metadata={"page": "1"}), 1.0246011368123038),
    ]


@pytest.mark.django_db()
def test_vectorstore_with_filter_no_match(experiment):
    """Test end to end construction and search."""
    texts = ["foo", "bar", "baz"]
    metadatas = [{"page": str(i)} for i in range(len(texts))]
    docsearch = PGVector.from_texts(
        texts=texts,
        embedding=DeterministicFakeEmbedding(size=ADA_TOKEN_COUNT),
        metadatas=metadatas,
        experiment=experiment,
    )
    output = docsearch.similarity_search_with_score("foo", k=1, filter={"page": "5"})
    assert output == []


def _remove_meta_fields(docs, fields=META_ALL):
    for doc in docs:
        if isinstance(doc, tuple):
            # (doc, score)
            doc = doc[0]
        for field in fields:
            doc.metadata.pop(field, None)

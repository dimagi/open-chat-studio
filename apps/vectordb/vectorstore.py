"""VectorStore wrapper around a Postgres/PGVector database.

Port of https://github.com/hwchase17/langchain/blob/master/langchain/vectorstores/pgvector.py
to use Django models.
"""
from __future__ import annotations

import enum
from collections.abc import Iterable
from typing import Any

from django.db.models import Q
from langchain.docstore.document import Document
from langchain.embeddings.base import Embeddings
from langchain.vectorstores.base import VectorStore
from pgvector.django import CosineDistance, L2Distance, MaxInnerProduct
from sentry_sdk import capture_exception

from apps.experiments.models import Experiment
from apps.teams.models import Team
from apps.utils.chunked import chunked

from .const import META_EMBEDDING_ID, META_EXPERIMENT_ID, META_FILE_ID, META_SEARCH_SCORE
from .models import Embedding


class QueryResult:
    Embedding: Embedding
    distance: float


class DistanceStrategy(enum.Enum):
    EUCLIDEAN = L2Distance
    COSINE = CosineDistance
    MAX_INNER_PRODUCT = MaxInnerProduct


DEFAULT_DISTANCE_STRATEGY = DistanceStrategy.COSINE


class PGVector(VectorStore):
    """
    VectorStore implementation using Postgres and pgvector.
    - `embedding_function` any embedding function implementing
        `langchain.embeddings.base.Embeddings` interface.
    - `distance_strategy` is the distance strategy to use. (default: EUCLIDEAN)
        - `EUCLIDEAN` is the euclidean distance.
        - `COSINE` is the cosine distance.
    """

    def __init__(
        self,
        experiment: Experiment,
        embedding_function: Embeddings,
        distance_strategy: DistanceStrategy = DEFAULT_DISTANCE_STRATEGY,
    ) -> None:
        self.experiment = experiment
        self.embedding_function = embedding_function
        self.distance_strategy = distance_strategy

    def delete_embeddings(self) -> None:
        self.experiment.embeddings.all().delete()

    def add_texts(self, texts: Iterable[str], metadatas: list[dict] | None = None, **kwargs) -> None:
        """Run more texts through the embeddings and add to the vectorstore.

        Args:
            texts: Iterable of strings to add to the vectorstore.
            metadatas: Optional list of metadatas associated with the texts.

        Returns:
            List of ids from adding the texts into the vectorstore.
        """
        if not metadatas:
            metadatas = [{} for _ in texts]

        for chunk_num, chunk in enumerate(chunked(zip(texts, metadatas), 500)):
            chunk_texts = [item[0] for item in chunk]
            embeddings = self.embedding_function.embed_documents(chunk_texts)
            for i in range(len(chunk)):
                text, metadata = chunk[i]
                embedding = embeddings[i]
                # fix postgres null character bug
                text = text.replace("\x00", "\uFFFD")
                try:
                    Embedding.objects.create(
                        team_id=self.experiment.team_id,
                        experiment=self.experiment,
                        embedding=embedding,
                        document=text,
                        metadata=metadata,
                        file_id=metadata.pop(META_FILE_ID, None),
                    )
                except Exception as e:
                    capture_exception(e)

    def similarity_search(
        self,
        query: str,
        k: int = 4,
        filter: dict | None = None,
        **kwargs: Any,
    ) -> list[Document]:
        """Run similarity search with PGVector with distance.

        Args:
            query (str): Query text to search for.
            k (int): Number of results to return. Defaults to 4.
            filter (Optional[Dict[str, str]]): Filter by metadata. Defaults to None.

        Returns:
            List of Documents most similar to the query.
        """
        embedding = self.embedding_function.embed_query(text=query)
        return self.similarity_search_by_vector(
            embedding=embedding,
            k=k,
            filter=filter,
        )

    def similarity_search_with_score(
        self,
        query: str,
        k: int = 4,
        filter: dict | None = None,
    ) -> list[tuple[Document, float]]:
        """Return docs most similar to query.

        Args:
            query: Text to look up documents similar to.
            k: Number of Documents to return. Defaults to 4.
            filter (Optional[Dict[str, str]]): Filter by metadata. Defaults to None.

        Returns:
            List of Documents most similar to the query and score for each
        """
        embedding = self.embedding_function.embed_query(query)
        docs = self.similarity_search_with_score_by_vector(embedding=embedding, k=k, filter=filter)
        return docs

    def similarity_search_with_score_by_vector(
        self,
        embedding: list[float],
        k: int = 4,
        filter: dict | None = None,
    ) -> list[tuple[Document, float]]:
        additional_filter = Q(experiment=self.experiment)
        if filter is not None:
            for key, value in filter.items():
                additional_filter &= Q(**{f"metadata__{key}": str(value)})

        return similarity_search_with_score_by_vector(
            team=self.experiment.team,
            embedding=embedding,
            k=k,
            distance_strategy=self.distance_strategy,
            additional_filter=additional_filter,
        )

    def similarity_search_by_vector(
        self,
        embedding: list[float],
        k: int = 4,
        filter: dict | None = None,
        **kwargs: Any,
    ) -> list[Document]:
        """Return docs most similar to embedding vector.

        Args:
            embedding: Embedding to look up documents similar to.
            k: Number of Documents to return. Defaults to 4.
            filter (Optional[Dict[str, str]]): Filter by metadata. Defaults to None.

        Returns:
            List of Documents most similar to the query vector.
        """
        docs_and_scores = self.similarity_search_with_score_by_vector(embedding=embedding, k=k, filter=filter)
        return [doc for doc, _ in docs_and_scores]

    @classmethod
    def from_texts(
        cls: type[PGVector],
        texts: list[str],
        embedding: Embeddings,
        metadatas: list[dict] | None = None,
        experiment: Experiment = None,
        distance_strategy: DistanceStrategy = DEFAULT_DISTANCE_STRATEGY,
        **kwargs,
    ) -> PGVector:
        """
        Return VectorStore initialized from texts and embeddings.
        """

        store = cls(
            experiment=experiment,
            embedding_function=embedding,
            distance_strategy=distance_strategy,
        )

        store.add_texts(texts=texts, metadatas=metadatas)
        return store

    @classmethod
    def from_documents(
        cls: type[PGVector],
        documents: list[Document],
        embedding: Embeddings,
        experiment: Experiment = None,
        **kwargs: Any,
    ) -> PGVector:
        """
        Return VectorStore initialized from documents and embeddings.
        """

        texts = [d.page_content for d in documents]
        metadatas = [d.metadata for d in documents]

        return cls.from_texts(
            texts=texts,
            embedding=embedding,
            metadatas=metadatas,
            experiment=experiment,
            **kwargs,
        )


def similarity_search_by_vector(
    team: Team,
    embedding: list[float],
    k: int = 4,
    distance_strategy: DistanceStrategy = DEFAULT_DISTANCE_STRATEGY,
    additional_filter: Q = None,
) -> list[Document]:
    docs_with_score = similarity_search_with_score_by_vector(
        team=team, embedding=embedding, k=k, distance_strategy=distance_strategy, additional_filter=additional_filter
    )
    return [doc for doc, _ in docs_with_score]


def similarity_search_with_score_by_vector(
    team: Team,
    embedding: list[float],
    k: int = 4,
    distance_strategy: DistanceStrategy = DEFAULT_DISTANCE_STRATEGY,
    additional_filter: Q = None,
) -> list[tuple[Document, float]]:
    query = Embedding.objects.filter(experiment__team=team)

    query = query.annotate(distance=distance_strategy.value("embedding", embedding)).order_by("distance")
    if additional_filter:
        query = query.filter(additional_filter)

    docs = [
        (
            Document(
                page_content=result.document,
                metadata={
                    META_EMBEDDING_ID: result.id,
                    META_EXPERIMENT_ID: result.experiment_id,
                    META_FILE_ID: result.file_id,
                    META_SEARCH_SCORE: result.distance,
                    **result.metadata,
                },
            ),
            result.distance,
        )
        for result in query[:k]
    ]

    return docs

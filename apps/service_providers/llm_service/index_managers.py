import contextlib
import logging
from abc import ABCMeta, abstractmethod
from collections.abc import Iterator

import openai
from django.conf import settings
from django.db import DatabaseError
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pgvector.django import CosineDistance

from apps.assistants.utils import chunk_list
from apps.documents.exceptions import FileUploadError
from apps.documents.models import CollectionFile, FileStatus
from apps.files.models import File, FileChunkEmbedding
from apps.service_providers.exceptions import UnableToLinkFileException

logger = logging.getLogger("ocs.index_manager")

Vector = list[float]


class IndexManager(metaclass=ABCMeta):
    @abstractmethod
    def add_files(
        self,
        collection_files: Iterator[CollectionFile],
        chunk_size: int = None,
        chunk_overlap: int = None,
    ):
        pass

    def delete_files(self, files: list[File]):
        """
        Remove files from the remote index service.

        Depending on the service implementation, this may only disassociate files
        from the vector store or completely delete them from remote storage.

        Args:
            files: List of File instances to delete from the remote service.
        """
        self.delete_files_from_index(files)

    @abstractmethod
    def delete_files_from_index(self, files: list[File]):
        """Disassociates the file with the vector store"""


class RemoteIndexManager(IndexManager, metaclass=ABCMeta):
    """
    Abstract base class for managing vector stores in remote indexing services.

    This class provides a common interface for interacting with remote vector stores,
    including operations like creating, retrieving, deleting, and managing files within
    the vector stores. Concrete implementations should handle the specifics of each
    remote service provider.
    """

    def __init__(self, index_id: str | None = None):
        self.index_id = index_id

    @abstractmethod
    def get(self):
        """
        Retrieve the vector store configuration and metadata from the remote index.

        Returns:
            The vector store object or configuration from the remote service.
        """
        ...

    @abstractmethod
    def delete_remote_index(self):
        """
        Delete the vector store from the remote index service.
        """
        ...

    @abstractmethod
    def link_files_to_remote_index(self, file_ids: list[str], chunk_size=None, chunk_overlap=None):
        """
        Associate files with the vector store in the remote index service.

        Args:
            file_ids: List of file's remote identifiers to link to the vector store.
            chunk_size: Optional maximum size for text chunks when processing files.
            chunk_overlap: Optional overlap size between consecutive chunks.
        """
        ...

    @abstractmethod
    def file_exists_at_remote(self, file: File) -> bool:
        """
        Check if a file exists at the remote index. This is used to determine if a file needs to be uploaded or not.
        Returns True if the file exists, False otherwise.
        """
        ...

    @abstractmethod
    def upload_file_to_remote(self, file: File):
        """
        Upload a file to the remote index service.

        This method handles the file upload process and should update the file's
        external_id attribute to reflect the remote service's file identifier.

        Args:
            file: The File instance to upload to the remote service.
        """
        ...

    def add_files(
        self,
        collection_files: Iterator[CollectionFile],
        chunk_size: int = None,
        chunk_overlap: int = None,
    ):
        uploaded_files: list[File] = []
        for collection_file in collection_files:
            file = collection_file.file
            try:
                self._ensure_remote_file_exists(file)
                uploaded_files.append(file)
            except FileUploadError:
                collection_file.status = FileStatus.FAILED
                collection_file.save(update_fields=["status"])

        try:
            self.link_files_to_remote_index(
                file_ids=[file.external_id for file in uploaded_files],
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
            CollectionFile.objects.filter(file_id__in=[file.id for file in uploaded_files]).update(
                status=FileStatus.COMPLETED
            )
        except UnableToLinkFileException:
            logger.exception("Failed to link files to remote index")
            CollectionFile.objects.filter(file_id__in=[file.id for file in uploaded_files]).update(
                status=FileStatus.FAILED
            )

    def _ensure_remote_file_exists(self, file: File):
        try:
            if not (file.external_id and self.file_exists_at_remote(file)):
                file.external_id = None
                self.upload_file_to_remote(file)
        except Exception:
            logger.exception(
                "Failed to upload file to the remote index",
                extra={
                    "file_id": file.id,
                    "team": file.team.slug,
                },
            )
            raise FileUploadError() from None


class OpenAIRemoteIndexManager(RemoteIndexManager):
    """
    OpenAI-specific implementation of RemoteIndexManager.

    This class provides concrete implementations for managing vector stores using
    OpenAI's vector store API. It handles file uploads, vector store creation,
    file linking with chunking strategies, and cleanup operations.
    """

    def __init__(self, client, index_id: str | None = None):
        super().__init__(index_id)
        self.client = client

    def get(self):
        return self.client.vector_stores.retrieve(self.index_id)

    def delete_remote_index(self):
        with contextlib.suppress(openai.NotFoundError):
            self.client.vector_stores.delete(vector_store_id=self.index_id)

    def delete_files_from_index(self, files: list[File]):
        """Disassociates the file with the vector store"""
        for file in files:
            self.delete_file_from_index(file.external_id)

    def delete_file_from_index(self, file_id: str):
        try:
            self.client.vector_stores.files.delete(vector_store_id=self.index_id, file_id=file_id)
        except Exception:
            logger.warning(
                "Failed to delete file from OpenAI vector store",
                extra={"vector_store_id": self.index_id, "file_id": file_id},
            )

    def link_files_to_remote_index(self, file_ids: list[str], chunk_size=None, chunk_overlap=None):
        """Link OpenAI files `file_ids` to the vector store in OpenAI."""
        chunking_strategy = None
        if chunk_size and chunk_overlap:
            chunking_strategy = {
                "type": "static",
                "static": {"max_chunk_size_tokens": chunk_size, "chunk_overlap_tokens": chunk_overlap},
            }

        try:
            for chunk in chunk_list(file_ids, 500):
                self.client.vector_stores.file_batches.create(
                    vector_store_id=self.index_id, file_ids=chunk, chunking_strategy=chunking_strategy
                )
        except Exception as e:
            logger.warning(
                "Failed to link files to OpenAI vector store",
                extra={"vector_store_id": self.index_id, "chunking_strategy": chunking_strategy},
            )
            raise UnableToLinkFileException("Failed to link files to OpenAI vector store") from e

    def file_exists_at_remote(self, file: File) -> bool:
        try:
            self.client.files.retrieve(file.external_id)
            return True
        except openai.NotFoundError:
            return False

    def upload_file_to_remote(self, file: File):
        from apps.assistants.sync import create_files_remote

        create_files_remote(self.client, files=[file])

    def delete_files(self, files: list[File]):
        """A convenience method to delete a files from the remote service"""
        for file in files:
            if not file.external_id:
                continue
            with contextlib.suppress(openai.NotFoundError):
                self.client.files.delete(file.external_id)

            file.external_id = ""
        File.objects.bulk_update(files, fields=["external_id"])


class LocalIndexManager(IndexManager, metaclass=ABCMeta):
    """
    Abstract base class for managing local embedding operations.

    This class provides a common interface for working with embedding models
    and text processing operations that can be performed locally, such as
    generating embedding vectors and chunking text content.
    """

    def __init__(self, api_key: str, embedding_model_name: str):
        self._api_key = api_key
        self.embedding_model_name = embedding_model_name

    @abstractmethod
    def get_embedding_vector(self, content: str) -> Vector:
        """
        Generate an embedding vector for the given text content.

        Args:
            content: The text content to generate embeddings for.

        Returns:
            Vector: A list of floats representing the embedding vector.
        """

    def add_files(
        self,
        collection_files: Iterator[CollectionFile],
        chunk_size: int = None,
        chunk_overlap: int = None,
    ):
        for collection_file in collection_files:
            file = collection_file.file
            embeddings = []
            try:
                text_chunks = self.chunk_file(file, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
                for idx, chunk in enumerate(text_chunks):
                    embedding_vector = self.get_embedding_vector(chunk)
                    embeddings.append(
                        FileChunkEmbedding.objects.create(
                            team_id=file.team_id,
                            file=file,
                            collection_id=collection_file.collection_id,
                            chunk_number=idx + 1,  # Start chunk numbering from 1
                            text=chunk,
                            embedding=embedding_vector,
                            # TODO: Get the page number if possible. Also, what file types are supported?
                            page_number=0,
                        )
                    )
                collection_file.status = FileStatus.COMPLETED
            except Exception as e:
                logger.exception("Failed to index file", extra={"file_id": file.id, "error": str(e)})
                collection_file.status = FileStatus.FAILED
            try:
                collection_file.save(update_fields=["status"])
            except DatabaseError:
                collection_file_id = collection_file.id
                collection_file = CollectionFile.objects.filter(id=collection_file_id).first()
                if not collection_file:
                    # collection file deleted - remove all the embeddings
                    FileChunkEmbedding.objects.filter(id__in=[embedding.id for embedding in embeddings]).delete()
                else:
                    logger.exception(
                        "Failed to update collection file status", extra={"collection_file_id": collection_file_id}
                    )

    def chunk_file(self, file: File, chunk_size: int, chunk_overlap: int) -> list[str]:
        """
        Split text content into overlapping chunks for processing.

        Args:
            text: The text content to be chunked.
            chunk_size: Maximum size of each text chunk.
            chunk_overlap: Number of characters/tokens to overlap between chunks.

        Returns:
            list[str]: List of text chunks with specified overlap.
        """

        text_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            model_name="gpt-4",
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

        documents = text_splitter.create_documents([file.read_content()])
        return [doc.page_content for doc in documents]

    def delete_files_from_index(self, files: list[File]):
        for file in files:
            self.delete_embeddings(file_id=file.id)

    def delete_embeddings(self, file_id: str):
        """Deleting a file from the local index doesn't really make"""
        FileChunkEmbedding.objects.filter(file__id=file_id).delete()

    def query(self, index_id: int, query: str, top_k: int = 5) -> list[FileChunkEmbedding]:
        """
        Query the local index for the most relevant file chunks based on the query string.

        Args:
            query: The query string to search for.
            top_k: The number of top results to return.

        Returns:
            list[FileChunkEmbedding]: List of FileChunkEmbedding instances matching the query.
        """

        embedding_vector = self.get_embedding_vector(query)
        return (
            FileChunkEmbedding.objects.annotate(distance=CosineDistance("embedding", embedding_vector))
            .filter(collection_id=index_id)
            .order_by("distance")
            .select_related("file")
            .only("text", "file__name")[:top_k]
        )


class OpenAILocalIndexManager(LocalIndexManager):
    """
    OpenAI-specific implementation of LocalIndexManager.

    This class provides concrete implementations for local embedding operations
    using OpenAI's embedding models and text processing utilities. It handles
    text chunking using tiktoken encoding and generates embeddings via OpenAI's API.
    """

    def get_embedding_vector(self, content: str) -> Vector:
        from langchain_openai import OpenAIEmbeddings

        embeddings = OpenAIEmbeddings(
            api_key=self._api_key, model=self.embedding_model_name, dimensions=settings.EMBEDDING_VECTOR_SIZE
        )
        return embeddings.embed_query(content)


class GoogleLocalIndexManager(LocalIndexManager):
    def get_embedding_vector(self, content: str) -> Vector:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        embeddings = GoogleGenerativeAIEmbeddings(
            google_api_key=self._api_key, model=f"models/{self.embedding_model_name}"
        )
        return embeddings.embed_query(content, output_dimensionality=settings.EMBEDDING_VECTOR_SIZE)

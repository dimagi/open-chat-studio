import contextlib
import logging
from abc import ABCMeta, abstractmethod

import openai
from django.conf import settings
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from apps.assistants.utils import chunk_list
from apps.documents.exceptions import FileUploadError
from apps.files.models import File
from apps.service_providers.exceptions import UnableToLinkFileException

logger = logging.getLogger("ocs.index_manager")

Vector = list[float]


class RemoteIndexManager(metaclass=ABCMeta):
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
    def create_remote_index(self, name: str, file_ids: list = None) -> str:
        """
        Create a new vector store in the remote index service.

        Args:
            name: The name to assign to the new vector store.
            file_ids: Optional list of remote file IDs to initially associate with the vector store.

        Returns:
            str: The unique identifier of the newly created vector store.
        """
        ...

    @abstractmethod
    def delete_remote_index(self, fail_silently: bool = False):
        """
        Delete the vector store from the remote index service.

        Args:
            fail_silently: If True, suppress exceptions when the vector store doesn't exist
                          or cannot be deleted. If False, raise exceptions on failures.
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

    @abstractmethod
    def delete_files(self, files: list[File]):
        """
        Remove files from the remote index service.

        Depending on the service implementation, this may only disassociate files
        from the vector store or completely delete them from remote storage.

        Args:
            files: List of File instances to delete from the remote service.
        """
        ...

    @abstractmethod
    def delete_file_from_index(self, file_id: str):
        """Disassociates the file with the vector store"""

    def ensure_remote_file_exists(self, file: File):
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

    def delete_vector_store(self, fail_silently: bool = False):
        try:
            self.delete_remote_index()
        except Exception as e:
            logger.warning("Vector store %s not found", self.index_id)
            if not fail_silently:
                raise e


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

    def create_remote_index(self, name: str, file_ids: list = None) -> str:
        file_ids = file_ids or []
        vector_store = self.client.vector_stores.create(name=name, file_ids=file_ids)
        self.index_id = vector_store.id
        return self.index_id

    def delete_remote_index(self):
        self.client.vector_stores.delete(vector_store_id=self.index_id)

    def delete_file_from_index(self, file_id: str):
        """Disassociates the file with the vector store"""
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
        """A convenience method to delete files from the remote service"""
        for file in files:
            with contextlib.suppress(openai.NotFoundError):
                self.client.files.delete(file.external_id)

            file.external_id = ""
        File.objects.bulk_update(files, fields=["external_id"])


class LocalIndexManager(metaclass=ABCMeta):
    """
    Abstract base class for managing local embedding operations.

    This class provides a common interface for working with embedding models
    and text processing operations that can be performed locally, such as
    generating embedding vectors and chunking text content.
    """

    def __init__(self, client: any, embedding_model_name: str):
        self.client = client
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

    @abstractmethod
    def chunk_content(self, text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
        """
        Split text content into overlapping chunks for processing.

        Args:
            text: The text content to be chunked.
            chunk_size: Maximum size of each text chunk.
            chunk_overlap: Number of characters/tokens to overlap between chunks.

        Returns:
            list[str]: List of text chunks with specified overlap.
        """


class OpenAILocalIndexManager(LocalIndexManager):
    """
    OpenAI-specific implementation of LocalIndexManager.

    This class provides concrete implementations for local embedding operations
    using OpenAI's embedding models and text processing utilities. It handles
    text chunking using tiktoken encoding and generates embeddings via OpenAI's API.
    """

    def get_embedding_vector(self, content: str) -> Vector:
        embeddings = OpenAIEmbeddings(
            api_key=self.client.api_key, model=self.embedding_model_name, dimensions=settings.EMBEDDING_VECTOR_SIZE
        )
        return embeddings.embed_query(content)

    def chunk_content(self, text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
        text_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            model_name="gpt-4",
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

        documents = text_splitter.create_documents([text])
        return [doc.page_content for doc in documents]

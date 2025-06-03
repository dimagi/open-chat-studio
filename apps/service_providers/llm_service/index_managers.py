import contextlib
import logging
from abc import abstractmethod

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


class RemoteIndexManager:
    def __init__(self, client, index_id: str | None = None):
        self.client = client
        self.index_id = index_id

    @abstractmethod
    def get(self):
        """Retrieve the vector store from the remote index."""
        ...

    @abstractmethod
    def create_vector_store(self, name: str, file_ids: list = None) -> str:
        """Create a new vector store in the remote index. Returns the vector store ID."""
        ...

    @abstractmethod
    def delete_remote_index(self, fail_silently: bool = False):
        """Delete the vector store from the remote index."""
        ...

    @abstractmethod
    def link_files_to_vector_store(self, file_ids: list[str], chunk_size=None, chunk_overlap=None) -> str:
        """Link files to the vector store in the remote index."""
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
        Upload a file to the remote index. This method should handle the file upload and set the `external_id` on the
        File instance.
        """
        ...

    @abstractmethod
    def delete_files(self, files: list[File]):
        """
        Delete files from the remote index. Depending on the service, this may only disassociate files from the index
        and not delete them from the remote storage as well.
        """
        ...

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
    def get(self):
        return self.client.vector_stores.retrieve(self.index_id)

    def create_vector_store(self, name: str, file_ids: list = None) -> str:
        file_ids = file_ids or []
        vector_store = self.client.vector_stores.create(name=name, file_ids=file_ids)
        self.index_id = vector_store.id
        return self.index_id

    def delete_remote_index(self):
        self.client.vector_stores.delete(vector_store_id=self.index_id)

    # TODO: Rename to remove ambiguious usage
    def delete_file(self, file_id: str):
        """Disassociates the file with the vector store"""
        with contextlib.suppress(Exception):
            self.client.vector_stores.files.delete(vector_store_id=self.index_id, file_id=file_id)

    def link_files_to_vector_store(self, file_ids: list[str], chunk_size=None, chunk_overlap=None) -> str:
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
            return self.index_id
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


class LocalIndexManager:
    def __init__(self, client: any, embedding_model_name: str):
        self.client = client
        self.embedding_model_name = embedding_model_name

    @abstractmethod
    def get_embedding_vector(self, content: str):
        """Get the embedding vector for the given content using the embedding model."""

    def chunk_content(self, text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
        """Chunk `text` according to `chunk_size` and `chunk_overlap`."""


class OpenAILocalIndexManager(LocalIndexManager):
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

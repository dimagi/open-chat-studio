import contextlib
import logging

import openai

from apps.assistants.utils import chunk_list
from apps.files.models import File
from apps.service_providers.exceptions import OpenAiUnableToLinkFileError

logger = logging.getLogger("ocs.index_manager")


class OpenAIVectorStoreManager:
    def __init__(self, client: openai.OpenAI):
        self.client = client

    def get(self, vector_store_id: str):
        return self.client.vector_stores.retrieve(vector_store_id)

    def create_vector_store(self, name: str, file_ids: list = None) -> str:
        file_ids = file_ids or []
        vector_store = self.client.vector_stores.create(name=name, file_ids=file_ids)
        return vector_store.id

    def delete_vector_store(self, vector_store_id: str, fail_silently: bool = False):
        try:
            self.client.vector_stores.delete(vector_store_id=vector_store_id)
        except (openai.NotFoundError, ValueError) as e:
            logger.warning("Vector store %s not found in OpenAI", vector_store_id)
            if not fail_silently:
                raise e

    def delete_file(self, vector_store_id: str, file_id: str):
        """Disassociates the file with the vector store"""
        try:
            self.client.vector_stores.files.delete(vector_store_id=vector_store_id, file_id=file_id)
        except openai.NotFoundError:
            logger.warning("File %s not found in OpenAI", file_id)

    def link_files_to_vector_store(
        self, vector_store_id: str, file_ids: list[str], chunk_size=None, chunk_overlap=None
    ) -> str:
        """Link OpenAI files `file_ids` to the vector store `vector_store_id` in OpenAI."""
        chunking_strategy = None
        if chunk_size and chunk_overlap:
            chunking_strategy = {
                "type": "static",
                "static": {"max_chunk_size_tokens": chunk_size, "chunk_overlap_tokens": chunk_overlap},
            }

        try:
            for chunk in chunk_list(file_ids, 50):
                self.client.vector_stores.file_batches.create(
                    vector_store_id=vector_store_id, file_ids=chunk, chunking_strategy=chunking_strategy
                )
            return vector_store_id
        except Exception as e:
            logger.warning(
                "Failed to link files to OpenAI vector store",
                extra={"vector_store_id": vector_store_id, "chunking_strategy": chunking_strategy},
            )
            raise OpenAiUnableToLinkFileError("Failed to link files to OpenAI vector store") from e

    def delete_files(self, files: list[File]):
        """A convenience method to delete files from the remote service"""
        for file in files:
            with contextlib.suppress(openai.NotFoundError):
                self.client.files.delete(file.external_id)

            file.external_id = ""
        File.objects.bulk_update(files, fields=["external_id"])

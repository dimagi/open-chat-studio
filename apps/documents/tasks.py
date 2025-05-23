import contextlib
import logging
from collections import defaultdict

import openai
from celery.app import shared_task
from taskbadger.celery import Task as TaskbadgerTask

from apps.assistants.sync import create_files_remote
from apps.documents.models import Collection, CollectionFile, FileStatus
from apps.service_providers.models import LlmProvider

logger = logging.getLogger("ocs.documents.tasks.upload_files_to_openai")


@shared_task(base=TaskbadgerTask, ignore_result=True)
def index_collection_files_task(collection_id: int):
    index_collection_files(collection_id, all_files=False)


@shared_task(base=TaskbadgerTask, ignore_result=True)
def migrate_vector_stores(collection_id: int, from_vector_store_id: str, from_llm_provider_id: int):
    """Migrate vector stores from one provider to another"""
    previous_remote_ids = index_collection_files(collection_id, all_files=True)
    _cleanup_old_vector_store(from_llm_provider_id, from_vector_store_id, previous_remote_ids)


def index_collection_files(collection_id: int, all_files: bool) -> list[str]:
    """
    Upload files to OpenAI and link them to the vector store. If `all_files` is `False`, only the files with a Pending
    status will be uploaded. If `all_files` is `True`, all files will be uploaded.
    The function will set the status of the files to `IN_PROGRESS` while uploading and `COMPLETED` when done.
    If the upload fails, the status will be set to `FAILED`.

    Returns a list of file ids that were previously linked to the files, if any.
    """
    collection = Collection.objects.prefetch_related("llm_provider").get(id=collection_id)
    client = collection.llm_provider.get_llm_service().get_raw_client()
    previous_remote_file_ids = []

    queryset = CollectionFile.objects.filter(collection=collection)
    if not all_files:
        queryset = queryset.filter(status=FileStatus.PENDING)

    # Link files to the new vector store
    # First, sort by chunking strategy
    strategy_file_map = defaultdict(list)
    default_chunking_strategy = {"chunk_size": 800, "chunk_overlap": 400}

    for collection_file in queryset.select_related("file").iterator(100):
        strategy = collection_file.metadata.get("chunking_strategy", default_chunking_strategy)
        strategy_file_map[(strategy["chunk_size"], strategy["chunk_overlap"])].append(collection_file)

        if collection_file.file.external_id:
            previous_remote_file_ids.append(collection_file.file.external_id)

    # Update the status of all files in queryset to IN_PROGRESS
    queryset.update(status=FileStatus.IN_PROGRESS)

    # Second, for each chunking strategy, upload files to the vector store
    for strategy_tuple, collection_files in strategy_file_map.items():
        chunk_size, chunk_overlap = strategy_tuple
        _upload_files_to_vector_store(
            client,
            collection,
            collection_files,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
    return previous_remote_file_ids


def _upload_files_to_vector_store(
    client, collection: Collection, collection_files: list[CollectionFile], chunk_size: int, chunk_overlap: int
):
    """Upload files to OpenAI and link them to the vector store"""
    file_ids = []
    collection_files_to_update = []
    vector_store_manager = collection.llm_provider.get_index_manager()

    for collection_file in collection_files:
        try:
            file = collection_file.file
            file.external_id = None
            remote_file_ids = create_files_remote(client, files=[file])
            collection_file.status = FileStatus.COMPLETED
            file_ids.extend(remote_file_ids)
        except Exception:
            logger.exception(
                "Failed to upload file to OpenAI",
                extra={
                    "file_id": collection_file.file.id,
                    "team": collection.team.slug,
                    "collection_id": collection.id,
                },
            )
            collection_file.status = FileStatus.FAILED

        collection_files_to_update.append(collection_file)

    try:
        vector_store_manager.link_files_to_vector_store(
            vector_store_id=collection.openai_vector_store_id,
            file_ids=file_ids,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    except Exception:
        logger.exception(
            "Failed to link files to vector store",
            extra={
                "file_ids": file_ids,
                "team": collection.team.slug,
                "collection_id": collection.id,
            },
        )
        for collection_file in collection_files_to_update:
            collection_file.status = FileStatus.FAILED

    CollectionFile.objects.bulk_update(collection_files_to_update, ["status"])


def _cleanup_old_vector_store(llm_provider_id: int, vector_store_id: str, file_ids: list[str]):
    llm_provider = LlmProvider.objects.get(id=llm_provider_id)
    old_manager = llm_provider.get_index_manager()
    old_manager.delete_vector_store(vector_store_id)

    for file_id in file_ids:
        with contextlib.suppress(openai.NotFoundError):
            old_manager.client.files.delete(file_id)

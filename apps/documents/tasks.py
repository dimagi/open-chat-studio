import contextlib
import logging
from collections import defaultdict

import openai
from celery.app import shared_task
from taskbadger.celery import Task as TaskbadgerTask

from apps.assistants.sync import OpenAiSyncError, create_files_remote
from apps.documents.exceptions import FileUploadError
from apps.documents.models import Collection, CollectionFile, FileStatus
from apps.service_providers.models import LlmProvider

logger = logging.getLogger("ocs.documents.tasks.upload_files_to_openai")

DEFAULT_CHUNKING_STRATEGY = {"chunk_size": 800, "chunk_overlap": 400}


@shared_task(base=TaskbadgerTask, ignore_result=True)
def index_collection_files_task(collection_id: int, retry_failed: bool = False):
    index_collection_files(collection_id, re_upload_all=False, retry_failed=retry_failed)


@shared_task(base=TaskbadgerTask, ignore_result=True)
def migrate_vector_stores(collection_id: int, from_vector_store_id: str, from_llm_provider_id: int):
    """Migrate vector stores from one provider to another"""
    previous_remote_ids = index_collection_files(collection_id, re_upload_all=True)
    _cleanup_old_vector_store(from_llm_provider_id, from_vector_store_id, previous_remote_ids)


def index_collection_files(collection_id: int, re_upload_all: bool, retry_failed: bool = False) -> list[str]:
    """Uploads files to the remote index.

    Args:
        collection_id: ID of the collection containing files to upload.
        re_upload_all: If True, uploads all files. If False, only uploads files with Pending status.
        retry_failed: If True and re_upload_all is False, only retries failed files.

    Returns:
        list[str]: List of file IDs that were previously linked to the files.

    Note:
        The function sets file status to IN_PROGRESS while uploading,
        COMPLETED when done, and FAILED if the upload fails.
    """
    # TODO: Preload collection files
    collection = Collection.objects.prefetch_related("llm_provider").get(id=collection_id)
    client = collection.llm_provider.get_llm_service().get_raw_client()
    previous_remote_file_ids = []

    queryset = CollectionFile.objects.filter(collection=collection)
    if not re_upload_all:
        scope_status = FileStatus.FAILED if retry_failed else FileStatus.PENDING
        queryset = queryset.filter(status=scope_status)

    # Link files to the new vector store
    # 1. Sort by chunking strategy
    strategy_file_map = defaultdict(list)

    for collection_file in queryset.select_related("file").iterator(100):
        strategy = collection_file.metadata.get("chunking_strategy", DEFAULT_CHUNKING_STRATEGY)
        strategy_file_map[(strategy["chunk_size"], strategy["chunk_overlap"])].append(collection_file)

        if collection_file.file.external_id:
            previous_remote_file_ids.append(collection_file.file.external_id)

    queryset.update(status=FileStatus.IN_PROGRESS)

    # 2. For each chunking strategy, upload files to the vector store
    for strategy_tuple, collection_files in strategy_file_map.items():
        chunk_size, chunk_overlap = strategy_tuple
        _upload_files_to_vector_store(
            client,
            collection,
            collection_files,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            re_upload_all=re_upload_all,
        )
    return previous_remote_file_ids


def _upload_files_to_vector_store(
    client,
    collection: Collection,
    collection_files: list[CollectionFile],
    chunk_size: int,
    chunk_overlap: int,
    re_upload_all: bool = False,
):
    """Upload files to OpenAI and link them to the vector store"""
    unlinked_collection_files = []
    vector_store_manager = collection.llm_provider.get_index_manager()

    for collection_file in collection_files:
        try:
            _ensure_remote_file_exists(client, collection_file=collection_file, re_upload_all=re_upload_all)
            unlinked_collection_files.append(collection_file)
        except FileUploadError:
            collection_file.status = FileStatus.FAILED
            collection_file.save(update_fields=["status"])

    try:
        file_ids = []
        for collection_file in unlinked_collection_files:
            file_ids.append(collection_file.file.external_id)

        vector_store_manager.link_files_to_vector_store(
            vector_store_id=collection.openai_vector_store_id,
            file_ids=file_ids,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        for collection_file in unlinked_collection_files:
            collection_file.status = FileStatus.COMPLETED

    except OpenAiSyncError:
        logger.exception(
            "Failed to link files to vector store",
            extra={
                "file_ids": file_ids,
                "team": collection.team.slug,
                "collection_id": collection.id,
            },
        )
        for collection_file in unlinked_collection_files:
            collection_file.status = FileStatus.FAILED

    CollectionFile.objects.bulk_update(unlinked_collection_files, ["status"])


def _cleanup_old_vector_store(llm_provider_id: int, vector_store_id: str, file_ids: list[str]):
    llm_provider = LlmProvider.objects.get(id=llm_provider_id)
    old_manager = llm_provider.get_index_manager()
    old_manager.delete_vector_store(vector_store_id)

    for file_id in file_ids:
        with contextlib.suppress(openai.NotFoundError):
            old_manager.client.files.delete(file_id)


def _ensure_remote_file_exists(client, collection_file: CollectionFile, re_upload_all: bool):
    try:
        file = collection_file.file
        if re_upload_all or not file.external_id:
            file.external_id = None
            create_files_remote(client, files=[file])
    except Exception:
        logger.exception(
            "Failed to upload file to OpenAI",
            extra={
                "file_id": collection_file.file.id,
                "team": collection_file.collection.team.slug,
                "collection_id": collection_file.collection.id,
            },
        )
        raise FileUploadError() from None

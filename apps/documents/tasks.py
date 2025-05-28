import contextlib
import logging
from itertools import groupby

import openai
from celery.app import shared_task
from django.db.models import QuerySet
from taskbadger.celery import Task as TaskbadgerTask

from apps.assistants.sync import create_files_remote
from apps.documents.exceptions import FileUploadError
from apps.documents.models import ChunkingStrategy, Collection, CollectionFile, FileStatus
from apps.service_providers.models import LlmProvider

logger = logging.getLogger("ocs.documents.tasks.link_files_to_index")


@shared_task(base=TaskbadgerTask, ignore_result=True)
def index_collection_files_task(collection_file_ids: list[int]):
    collection_files = CollectionFile.objects.filter(id__in=collection_file_ids)
    index_collection_files(collection_files_queryset=collection_files)


@shared_task(base=TaskbadgerTask, ignore_result=True)
def migrate_vector_stores(collection_id: int, from_vector_store_id: str, from_llm_provider_id: int):
    """Migrate vector stores from one provider to another"""
    collection_files = CollectionFile.objects.filter(collection_id=collection_id)
    previous_remote_ids = index_collection_files(collection_files_queryset=collection_files, re_upload=True)
    _cleanup_old_vector_store(from_llm_provider_id, from_vector_store_id, previous_remote_ids)


def index_collection_files(collection_files_queryset: QuerySet[CollectionFile], re_upload: bool = False) -> list[str]:
    """Uploads files to the remote index.

    Args:
        collection_files_queryset: The queryset of `CollectionFile` objects to be indexed.
        re_upload: If True, the files will be re-uploaded to the index
    Returns:
        list[str]: List of file IDs that were previously linked to the files.

    Note:
        The function sets file status to IN_PROGRESS while uploading,
        COMPLETED when done, and FAILED if the upload fails.
    """
    collection_file = collection_files_queryset.first()
    if not collection_file:
        return []

    collection = collection_file.collection
    client = collection.llm_provider.get_llm_service().get_raw_client()
    previous_remote_file_ids = []

    default_chunking_strategy = ChunkingStrategy(chunk_size=800, chunk_overlap=400)
    strategy_groups = groupby(
        collection_files_queryset.select_related("file").iterator(100),
        lambda cf: cf.chunking_strategy or default_chunking_strategy,
    )

    for strategy, collection_files_group in strategy_groups:
        collection_files = list(collection_files_group)
        ids = []
        for collection_file in collection_files:
            ids.append(collection_file.id)
            if collection_file.file.external_id:
                previous_remote_file_ids.append(collection_file.file.external_id)

        CollectionFile.objects.filter(id__in=ids).update(status=FileStatus.IN_PROGRESS)

        _upload_files_to_vector_store(
            client,
            collection,
            collection_files,
            chunk_size=strategy.chunk_size,
            chunk_overlap=strategy.chunk_overlap,
            re_upload=re_upload,
        )
    return previous_remote_file_ids


def _upload_files_to_vector_store(
    client,
    collection: Collection,
    collection_files: list[CollectionFile],
    chunk_size: int,
    chunk_overlap: int,
    re_upload: bool = False,
):
    """Upload files to the remote index"""
    unlinked_collection_files = []
    vector_store_manager = collection.llm_provider.get_index_manager()

    for collection_file in collection_files:
        try:
            _ensure_remote_file_exists(client, collection_file=collection_file, re_upload=re_upload)
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

    except Exception:
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


def _ensure_remote_file_exists(client, collection_file: CollectionFile, re_upload: bool):
    try:
        file = collection_file.file
        if re_upload or not file.external_id:
            file.external_id = None
            create_files_remote(client, files=[file])
    except Exception:
        logger.exception(
            "Failed to upload file to the remote index",
            extra={
                "file_id": collection_file.file.id,
                "team": collection_file.collection.team.slug,
                "collection_id": collection_file.collection.id,
            },
        )
        raise FileUploadError() from None

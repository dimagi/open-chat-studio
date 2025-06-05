import contextlib
import logging
from itertools import groupby

import openai
from celery.app import shared_task
from django.db.models import QuerySet
from taskbadger.celery import Task as TaskbadgerTask

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
    previous_remote_ids = index_collection_files(collection_files_queryset=collection_files)

    collection = Collection.objects.get(id=collection_id)
    if collection.is_remote_index:
        _cleanup_old_vector_store(from_llm_provider_id, from_vector_store_id, previous_remote_ids)


def index_collection_files(collection_files_queryset: QuerySet[CollectionFile]) -> list[str]:
    """Add files to the collection index.

    Args:
        collection_files_queryset: The queryset of `CollectionFile` objects to be indexed.
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
    previous_remote_file_ids = []

    default_chunking_strategy = ChunkingStrategy(chunk_size=800, chunk_overlap=400)
    strategy_groups = groupby(
        collection_files_queryset.select_related("file").iterator(100),
        lambda cf: cf.chunking_strategy or default_chunking_strategy,
    )

    for strategy, collection_files_group in strategy_groups:
        ids = []
        for collection_file in collection_files_group:
            ids.append(collection_file.id)
            if collection_file.file.external_id:
                previous_remote_file_ids.append(collection_file.file.external_id)

        CollectionFile.objects.filter(id__in=ids).update(status=FileStatus.IN_PROGRESS)

        collection.add_files_to_index(
            collection_files=CollectionFile.objects.filter(id__in=ids).iterator(100),
            chunk_size=strategy.chunk_size,
            chunk_overlap=strategy.chunk_overlap,
        )

    return previous_remote_file_ids


def _cleanup_old_vector_store(llm_provider_id: int, vector_store_id: str, file_ids: list[str]):
    llm_provider = LlmProvider.objects.get(id=llm_provider_id)
    old_manager = llm_provider.get_remote_index_manager(vector_store_id)
    old_manager.delete_vector_store()

    for file_id in file_ids:
        with contextlib.suppress(openai.NotFoundError):
            old_manager.client.files.delete(file_id)

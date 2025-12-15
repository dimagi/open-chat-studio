import contextlib
import logging
import zipfile
from datetime import timedelta
from io import BytesIO
from itertools import groupby

import openai
from celery.app import shared_task
from celery_progress.backend import ProgressRecorder
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from django.utils.text import slugify
from taskbadger.celery import Task as TaskbadgerTask

from apps.assistants.models import OpenAiAssistant
from apps.documents.datamodels import ChunkingStrategy, CollectionFileMetadata
from apps.documents.models import (
    Collection,
    CollectionFile,
    DocumentSource,
    FileStatus,
)
from apps.documents.utils import bulk_delete_collection_files
from apps.files.models import File
from apps.service_providers.models import LlmProvider
from apps.utils.celery import TaskbadgerTaskWrapper

logger = logging.getLogger("ocs.documents.tasks.link_files_to_index")


@shared_task(ignore_result=True)
def index_collection_files_task(collection_file_ids: list[int]):
    collection_files = CollectionFile.objects.filter(id__in=collection_file_ids)
    index_collection_files(collection_files_queryset=collection_files)


@shared_task(ignore_result=True)
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
            collection_files=CollectionFile.objects.filter(id__in=ids).select_related("file").iterator(100),
            chunk_size=strategy.chunk_size,
            chunk_overlap=strategy.chunk_overlap,
        )

    return previous_remote_file_ids


def _cleanup_old_vector_store(llm_provider_id: int, vector_store_id: str, file_ids: list[str]):
    llm_provider = LlmProvider.objects.get(id=llm_provider_id)
    old_manager = llm_provider.get_remote_index_manager(vector_store_id)
    old_manager.delete_remote_index()

    for file_id in file_ids:
        with contextlib.suppress(openai.NotFoundError):
            old_manager.client.files.delete(file_id)


@shared_task(ignore_result=True)
def create_collection_from_assistant_task(collection_id: int, assistant_id: int):
    """Create a collection from an assistant's file search resources"""
    # Get file search resources from the assistant
    collection = Collection.objects.get(id=collection_id)
    assistant = OpenAiAssistant.objects.get(id=assistant_id)
    file_search_resource = assistant.tool_resources.filter(tool_type="file_search").first()

    if not file_search_resource:
        # This will never happen, but just in case
        return

    # Add files to the collection
    # Create CollectionFile entries
    collection_files = []
    file_with_remote_ids = []
    file_without_remote_ids = []
    for file in file_search_resource.files.all():
        if file.external_id:
            file_with_remote_ids.append(file)
        else:
            file_without_remote_ids.append(file)

        collection_files.append(
            CollectionFile(
                collection=collection,
                file=file,
                status=FileStatus.PENDING,
                metadata=CollectionFileMetadata(chunking_strategy=ChunkingStrategy(chunk_size=800, chunk_overlap=400)),
            )
        )
    CollectionFile.objects.bulk_create(collection_files)

    try:
        # Create vector store for the collection
        collection.ensure_remote_index_created()
        index_manager = collection.get_index_manager()

        # Link files to the new vector store at OpenAI (only if there are files with external IDs)
        if file_with_remote_ids:
            index_manager.link_files_to_remote_index(
                file_ids=[file.external_id for file in file_with_remote_ids],
            )
            # Update status to completed for successfully linked files
            CollectionFile.objects.filter(collection=collection, file__in=file_with_remote_ids).update(
                status=FileStatus.COMPLETED
            )

    except Exception:
        logger.exception("Failed to link files to vector store")
        # Mark files as failed
        if file_with_remote_ids:
            CollectionFile.objects.filter(collection=collection, file__in=file_with_remote_ids).update(
                status=FileStatus.FAILED
            )

    # Index files that don't have external IDs
    if file_without_remote_ids:
        file_ids_to_index = list(
            CollectionFile.objects.filter(file__in=file_without_remote_ids).values_list("id", flat=True)
        )
        index_collection_files_task(collection_file_ids=file_ids_to_index)


@shared_task(ignore_result=True)
def sync_document_source_task(document_source_id: int):
    """Sync a specific document source"""
    from apps.documents.document_source_service import sync_document_source

    try:
        document_source = DocumentSource.objects.select_related("collection").get(id=document_source_id)
    except DocumentSource.DoesNotExist:
        return

    try:
        result = sync_document_source(document_source)

        if result.success:
            logger.info(
                f"Document source sync completed for {document_source}: "
                f"{result.files_added} added, {result.files_updated} updated, "
                f"{result.files_removed} removed"
            )
        else:
            logger.error(f"Document source sync failed for {document_source}: {result.error_message}")
    except Exception:
        logger.exception(
            "Unexpected error syncing document source",
            extra={
                "document_source": document_source_id,
            },
        )

    document_source.sync_task_id = ""
    document_source.save(update_fields=["sync_task_id"])


@shared_task(ignore_result=True)
def sync_all_document_sources_task():
    """Sync all document sources that have auto_sync_enabled=True"""
    auto_sources = DocumentSource.objects.filter(
        auto_sync_enabled=True,
        collection__is_index=True,  # Only sync indexed collections
    ).values_list("id", flat=True)

    sync_document_source_task.map(auto_sources).delay()


@shared_task(
    bind=True,
    base=TaskbadgerTask,
    acks_late=True,
    ignore_result=True,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
)
def delete_collection_task(self, collection_id: int):
    try:
        collection = Collection.objects.get(id=collection_id)
    except Collection.DoesNotExist:
        return

    if not collection.is_archived:
        logger.warning(
            "Attempting to delete an unarchived collection",
            extra={
                "collection": collection,
            },
        )
        return

    tb_task = TaskbadgerTaskWrapper(self)
    paginator = Paginator(collection.collectionfile_set.all(), per_page=100, orphans=25)
    for page in paginator:
        with transaction.atomic():
            bulk_delete_collection_files(collection, page.object_list, is_index_deletion=True)
        tb_task.set_progress(page.number, paginator.num_pages)

    if collection.is_index and collection.openai_vector_store_id:
        collection.remove_remote_index()


@shared_task(
    bind=True,
    base=TaskbadgerTask,
    acks_late=True,
    ignore_result=True,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
)
def delete_document_source_task(self, document_source_id: int):
    """Delete or archive a DocumentSource and it's files"""
    try:
        document_source = DocumentSource.objects.get_all().select_related("collection").get(id=document_source_id)
    except DocumentSource.DoesNotExist:
        return

    if not document_source.is_archived:
        logger.warning(
            "Attempting to delete resources from an unarchived document source",
            extra={
                "document_source": document_source_id,
            },
        )
        return

    tb_task = TaskbadgerTaskWrapper(self)
    paginator = Paginator(document_source.collectionfile_set.all(), per_page=100, orphans=25)
    for page in paginator:
        with transaction.atomic():
            bulk_delete_collection_files(document_source.collection, page.object_list)
        tb_task.set_progress(page.number, paginator.num_pages)

    if not document_source.has_versions:
        document_source.delete()


@shared_task(
    bind=True,
    base=TaskbadgerTask,
    acks_late=True,
    ignore_result=False,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
)
def create_collection_zip_task(self, collection_id: int, team_id: int):
    """
    Create a ZIP file containing all manually uploaded files from a collection.
    """
    progress_recorder = ProgressRecorder(self)

    try:
        collection = Collection.objects.get(id=collection_id)
    except Collection.DoesNotExist:
        logger.error(f"Collection {collection_id} not found")
        return None

    # Get all manually uploaded files (excluding document source files)
    collection_files = CollectionFile.objects.filter(
        collection=collection, document_source__isnull=True
    ).select_related("file")

    total_files = collection_files.count()

    if total_files == 0:
        logger.warning(f"No manually uploaded files found in collection {collection_id}")
        return None

    zip_buffer = BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        # Track filenames to handle duplicates
        used_filenames = {}

        for idx, collection_file in enumerate(collection_files, start=1):
            file = collection_file.file
            filename = file.name

            # Handle duplicate filenames
            if filename in used_filenames:
                used_filenames[filename] += 1
                name_parts = filename.rsplit(".", 1)
                if len(name_parts) == 2:
                    filename = f"{name_parts[0]}_{used_filenames[filename]}.{name_parts[1]}"
                else:
                    filename = f"{filename}_{used_filenames[filename]}"
            else:
                used_filenames[filename] = 0

            try:
                with file.file.open("rb") as f:
                    zip_file.writestr(filename, f.read())
            except Exception as e:
                logger.error(f"Error adding file {file.id} to ZIP: {str(e)}")

            progress_recorder.set_progress(idx, total_files, description=f"Adding {filename}")

    zip_buffer.seek(0)
    timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
    zip_filename = f"{slugify(collection.name)}_files_{timestamp}.zip"

    expiry_date = timezone.now() + timedelta(hours=24)

    zip_file_obj = File.objects.create(
        team_id=team_id,
        name=zip_filename,
        file=ContentFile(zip_buffer.getvalue(), name=zip_filename),
        content_type="application/zip",
        expiry_date=expiry_date,
    )

    logger.info(f"Created ZIP file {zip_file_obj.id} for collection {collection_id}")

    return zip_file_obj.id

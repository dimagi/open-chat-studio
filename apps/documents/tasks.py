import logging
from collections import defaultdict

from celery.app import shared_task
from taskbadger.celery import Task as TaskbadgerTask

from apps.assistants.sync import VectorStoreManager, create_files_remote
from apps.documents.models import Collection, CollectionFile, FileStatus
from apps.service_providers.models import LlmProvider

logger = logging.getLogger("ocs.documents.tasks.upload_files_to_openai")


@shared_task(base=TaskbadgerTask, ignore_result=True)
def upload_files_to_vector_store_task(collection_file_ids: list[int], chuking_strategy: dict):
    CollectionFile.objects.filter(id__in=collection_file_ids, status=FileStatus.PENDING).update(
        status=FileStatus.IN_PROGRESS
    )

    collection_files = CollectionFile.objects.filter(id__in=collection_file_ids).all()
    collection = collection_files[0].collection
    client = collection.llm_provider.get_llm_service().get_raw_client()
    _upload_files_to_vector_store(
        client,
        collection,
        collection_files,
        chunk_size=chuking_strategy["chunk_size"],
        chunk_overlap=chuking_strategy["chunk_overlap"],
    )


@shared_task(base=TaskbadgerTask, ignore_result=True)
def migrate_vector_stores(collection_id: int, from_vector_store_id: str, from_llm_provider_id: int):
    """Migrate vector stores from one provider to another"""
    # Select related, the file
    collection = Collection.objects.prefetch_related("llm_provider").get(id=collection_id)
    new_provider_client = collection.llm_provider.get_llm_service().get_raw_client()

    # Set in progress status
    queryset = CollectionFile.objects.filter(collection_id=collection_id)
    queryset.update(status=FileStatus.IN_PROGRESS)

    old_file_references = []

    # Link files to the new vector store
    # First, sort by chunking strategy
    strategy_file_map = defaultdict(list)
    for collection_file in queryset.select_related("file").iterator(100):
        old_file_references.append(collection_file.file.external_id)
        strategy = collection_file.metadata["chunking_strategy"]
        strategy_file_map[(strategy["chunk_size"], strategy["chunk_overlap"])].append(collection_file)

    # Second, for each chunking strategy, upload files to the vector store
    for strategy_tuple, collection_files in strategy_file_map.items():
        chunk_size, chunk_overlap = strategy_tuple
        _upload_files_to_vector_store(
            new_provider_client, collection, collection_files, chunk_size=chunk_size, chunk_overlap=chunk_overlap
        )

    _cleanup_old_vector_store(from_llm_provider_id, from_vector_store_id, old_file_references)
    # Cleanup of the old vector store and files


def _cleanup_old_vector_store(llm_provider_id: int, vector_store_id: str, file_ids: list[str]):
    old_manager = VectorStoreManager.from_llm_provider(LlmProvider.objects.get(id=llm_provider_id))
    old_manager.delete_vector_store(vector_store_id)

    for file_id in file_ids:
        old_manager.client.files.delete(file_id)


def _upload_files_to_vector_store(
    client, collection: Collection, collection_files: list[CollectionFile], chunk_size: int, chunk_overlap: int
):
    """Upload files to OpenAI and link them to the vector store"""
    file_ids = []
    collection_files_to_update = []
    vector_store_manager = VectorStoreManager(client)

    for collection_file in collection_files:
        try:
            file = collection_file.file
            file.external_id = None
            remote_file_ids = create_files_remote(client, files=[file])
            collection_file.status = FileStatus.COMPLETED
            file_ids.extend(remote_file_ids)
        except Exception as e:
            logger.exception(f"Failed to upload file {collection_file.file.id} to OpenAI: {e}")
            collection_file.status = FileStatus.FAILED

        collection_files_to_update.append(collection_file)

    vector_store_manager.link_files_to_vector_store(
        vector_store_id=collection.openai_vector_store_id,
        file_ids=file_ids,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    CollectionFile.objects.bulk_update(collection_files_to_update, ["status"])

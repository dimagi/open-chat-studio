import logging

from celery.app import shared_task
from taskbadger.celery import Task as TaskbadgerTask

from apps.assistants.sync import VectorStoreManager, create_files_remote
from apps.documents.models import CollectionFile, FileStatus

logger = logging.getLogger("ocs.documents.tasks.upload_files_to_openai")


@shared_task(base=TaskbadgerTask, ignore_result=True)
def upload_files_to_vector_store_task(collection_file_ids: list[int], chuking_strategy: dict):
    CollectionFile.objects.filter(id__in=collection_file_ids, status=FileStatus.PENDING).update(
        status=FileStatus.IN_PROGRESS
    )

    collection_files = CollectionFile.objects.filter(id__in=collection_file_ids).all()
    collection = collection_files[0].collection
    client = collection.llm_provider.get_llm_service().get_raw_client()

    file_ids = []
    collection_files_to_update = []
    vector_store_manager = VectorStoreManager(client)

    for collection_file in collection_files:
        try:
            remote_file_id = create_files_remote(client, files=[collection_file.file])
            collection_file.status = FileStatus.COMPLETED
            file_ids.extend(remote_file_id)
        except Exception as e:
            logger.exception(f"Failed to upload file {collection_file.file.id} to OpenAI: {e}")
            collection_file.status = FileStatus.FAILED

        collection_files_to_update.append(collection_file)

    vector_store_manager.link_files_to_vector_store(
        vector_store_id=collection.openai_vector_store_id,
        file_ids=file_ids,
        chunk_size=chuking_strategy["chunk_size"],
        chunk_overlap=chuking_strategy["chunk_overlap"],
    )
    CollectionFile.objects.bulk_update(collection_files_to_update, ["status"])

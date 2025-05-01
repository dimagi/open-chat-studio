import logging

from celery.app import shared_task
from taskbadger.celery import Task as TaskbadgerTask

from apps.assistants.sync import create_files_remote
from apps.documents.models import CollectionFile, FileStatus

logger = logging.getLogger("ocs.documents.tasks.upload_files_to_openai")


@shared_task(base=TaskbadgerTask)
def upload_files_to_vector_store_task(collection_file_ids: list[int]):
    # TODO: Test
    """
    1. Make sure we can upload the file to OpenAI
    """
    CollectionFile.objects.filter(id__in=collection_file_ids, status=FileStatus.PENDING).update(
        status=FileStatus.IN_PROGRESS
    )

    collection_files = CollectionFile.objects.filter(id__in=collection_file_ids).all()
    collection = collection_files[0].collection
    client = collection.llm_provider.get_llm_service().get_raw_client()

    for collection_file in collection_files:
        try:
            create_files_remote(client, files=[collection_file.file])
            collection_file.status = FileStatus.COMPLETED
        except Exception as e:
            logger.exception(f"Failed to upload file {collection_file.file.id} to OpenAI: {e}")
            collection_file.status = FileStatus.FAILED

        collection_file.save(update_fields=["status"])

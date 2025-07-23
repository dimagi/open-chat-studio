from apps.documents.models import CollectionFile
from apps.files.models import File
from apps.utils.deletion import get_related_m2m_objects


def delete_collection_file(collection_file: CollectionFile):
    """Delete an individual collection file. This handles the file deletion
    as well as removing the file from the collection index if necessary."""
    file = collection_file.file
    collection = collection_file.collection
    collection_file.delete()

    if file.is_used():
        if collection.is_index:
            # Remove it from the index only
            index_manager = collection.get_index_manager()
            index_manager.delete_files_from_index(files=[file])
    else:
        # Nothing else is using it
        if collection.is_index:
            index_manager = collection.get_index_manager()
            index_manager.delete_files(files=[file])

        file.delete_or_archive()


def bulk_delete_collection_files(collection, collection_files: list[CollectionFile]):
    """Bulk delete collection files. This handles the file deletion
    as well as removing the file from the collection index if necessary."""
    files = [collection_file.file for collection_file in collection_files]
    files_in_use = get_related_m2m_objects(files)

    CollectionFile.objects.filter(id__in=[file.id for file in collection_files]).delete()

    index_manager = collection.get_index_manager()
    index_only_delete = [file for file in files if file in files_in_use]
    full_delete = [file for file in files if file not in files_in_use]
    if index_only_delete and collection.is_index:
        index_manager.delete_files_from_index(files=index_only_delete)

    if full_delete:
        if collection.is_index:
            index_manager.delete_files(files=full_delete)

        file_ids = {file.id for file in full_delete}
        files_with_versions = File.objects.filter(working_version__in=full_delete).values("working_version__id").distinct()
        to_delete = file_ids - files_with_versions
        File.objects.filter(id__in=to_delete).delete()
        File.objects.filter(id__in=files_with_versions).update(is_archived=True)

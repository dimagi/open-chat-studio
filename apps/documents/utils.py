from apps.documents.models import CollectionFile


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

from collections.abc import Iterator

from apps.files.models import File


def duplicate_files(file_iterator: Iterator[File]) -> list[str]:
    """
    Duplicate files from the given file iterator.

    Args:
        file_iterator (iterator): An iterator that yields file objects to be duplicated.

    Returns:
        list: A list of IDs of the duplicated files.
    """
    file_ids = []
    for file in file_iterator:
        new_file = file.duplicate()
        file_ids.append(new_file.id)
        del new_file  # force memory cleanup
    return file_ids

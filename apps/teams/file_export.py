import logging
from collections.abc import Iterator

from zipstream import ZIP_DEFLATED, ZipStream

from apps.files.models import File, FilePurpose

logger = logging.getLogger("ocs.teams")

CHUNK_SIZE = 64 * 1024


def get_team_files_queryset(team):
    """Current working files for the team, excluding export artifacts.

    The File manager already excludes archived files; we additionally limit to
    working versions and drop previously generated export zips.
    """
    return (
        File.objects.filter(team=team, working_version__isnull=True)
        .exclude(purpose=FilePurpose.DATA_EXPORT)
        .order_by("id")
    )


def _file_chunks(file: File) -> Iterator[bytes]:
    """Yield a stored file's bytes in chunks, opening it lazily."""
    try:
        with file.file.open("rb") as fh:
            while chunk := fh.read(CHUNK_SIZE):
                yield chunk
    except Exception:
        logger.exception("Skipping file id=%s while streaming export", file.id)


def stream_team_files_zip(team) -> Iterator[bytes]:
    """Stream a compressed zip of all the team's current files.

    Entry paths mirror each file's storage path (``file.file.name``) so the
    archive can be re-imported into a storage backend with an identical layout.
    """
    zip_stream = ZipStream(compress_type=ZIP_DEFLATED)
    seen: set[str] = set()
    for file in get_team_files_queryset(team).iterator():
        if not file.file:
            continue
        arcname = file.file.name
        if arcname in seen:
            logger.warning("Skipping duplicate export path %s (file id=%s)", arcname, file.id)
            continue
        # One storage HEAD per file before streaming starts. On S3 that's O(n)
        # round-trips up front, but it lets us cleanly skip missing files rather
        # than emit a zero-byte entry. Acceptable here: gunicorn runs with no
        # request timeout and teams aren't expected to have pathologically many files.
        if not file.file.storage.exists(arcname):
            logger.warning("Skipping missing file %s (id=%s) from export", arcname, file.id)
            continue
        seen.add(arcname)
        zip_stream.add(data=_file_chunks(file), arcname=arcname)
    yield from zip_stream

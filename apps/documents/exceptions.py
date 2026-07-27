class FileUploadError(Exception):
    pass


class DocumentSourceDeleted(Exception):
    """Raised when a DocumentSource is deleted (or archived) while a sync is in progress.

    A long-running sync fetches the source once at the start; if a user deletes it
    mid-sync, subsequent writes against the now-dangling foreign key would fail with a
    database error. Rather than crash or churn through wasted work, the sync detects the
    deletion, raises this, and the whole task is aborted cleanly.
    """

    pass


class IndexConfigurationException(Exception):
    pass


class ZipCreationError(Exception):
    """Raised on transient file read failures during ZIP creation.

    Triggers Celery autoretry. After max_retries the task is marked
    FAILURE and the frontend error panel is shown.
    """

    pass


class ZipIntegrityError(ZipCreationError):
    """Raised when a file's byte count does not match its recorded content_size.

    This is a permanent data-integrity failure — retrying will not change the
    stored metadata, so the task decorator excludes this from autoretry via
    dont_autoretry_for.
    """

    pass

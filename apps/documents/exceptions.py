class FileUploadError(Exception):
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

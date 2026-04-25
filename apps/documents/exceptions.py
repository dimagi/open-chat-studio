class FileUploadError(Exception):
    pass


class IndexConfigurationException(Exception):
    pass


class ZipCreationError(Exception):
    """Raised when a file cannot be processed during ZIP archive creation.

    Causes the Celery task to fail and be retried. After max_retries the
    task is marked as FAILURE and the frontend error panel is shown.
    """

    pass

from django.core.files.storage import storages  # ty: ignore[unresolved-import]
from storages.backends.s3 import S3Storage


class PublicMediaStorage(S3Storage):
    file_overwrite = False


class PrivateMediaStorage(S3Storage):
    file_overwrite = False
    custom_domain = False

    def get_object_parameters(self, name):
        params = super().get_object_parameters(name)
        # For .gz uploads, set ContentType explicitly so django-storages skips its
        # filename-based auto-detection — which would otherwise set Content-Encoding: gzip
        # and cause HTTP clients to transparently decompress the file on download.
        if name.lower().endswith(".gz") and "ContentType" not in params:
            params["ContentType"] = "application/gzip"
        return params


def get_public_media_storage():
    return storages["public"]

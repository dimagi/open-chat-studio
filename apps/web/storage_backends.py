from django.core.files.storage import storages  # ty: ignore[unresolved-import]
from storages.backends.s3 import S3Storage


class PublicMediaStorage(S3Storage):
    file_overwrite = False


class PrivateMediaStorage(S3Storage):
    file_overwrite = False
    custom_domain = False


def get_public_media_storage():
    return storages["public"]

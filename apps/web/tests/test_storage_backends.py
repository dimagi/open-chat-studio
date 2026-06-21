from django.test import override_settings

from apps.web.storage_backends import PrivateMediaStorage, PublicMediaStorage


def test_gz_uploads_do_not_set_content_encoding():
    """Regression: django-storages auto-detects ContentEncoding=gzip from a .gz filename
    and HTTP clients then auto-decompress on download, defeating the point of compression
    for our gzipped CSV exports.
    """
    storage = PrivateMediaStorage(bucket_name="test-bucket")

    params = storage._get_write_parameters("exports/chat-export.csv.gz")

    assert params["ContentType"] == "application/gzip"
    assert "ContentEncoding" not in params


def test_non_gz_uploads_use_default_detection():
    storage = PrivateMediaStorage(bucket_name="test-bucket")

    params = storage._get_write_parameters("exports/chat-export.csv")

    assert params["ContentType"] == "text/csv"
    assert "ContentEncoding" not in params


@override_settings(
    AWS_S3_ENDPOINT_URL="http://minio:9000",
    AWS_ACCESS_KEY_ID="test-key",
    AWS_SECRET_ACCESS_KEY="test-secret",
    AWS_S3_REGION="us-east-1",
)
def test_private_storage_presigned_url_uses_endpoint():
    """S3-compatible endpoints (MinIO, R2, etc.) flow into private presigned URLs."""
    storage = PrivateMediaStorage(bucket_name="test-bucket")

    url = storage.url("exports/chat-export.csv")

    # Presigned URL generation is local (no network); it must target the custom endpoint
    # (not AWS). boto3 may use path- or virtual-host style, so we assert on the host.
    assert "minio:9000" in url
    assert "amazonaws.com" not in url


@override_settings(AWS_S3_CUSTOM_DOMAIN="cdn.example.com/public-bucket")
def test_public_storage_uses_custom_domain():
    """Public media URLs are built from AWS_S3_CUSTOM_DOMAIN (works for path-style and virtual-host)."""
    storage = PublicMediaStorage(bucket_name="public-bucket")

    url = storage.url("media/avatar.png")

    assert url == "https://cdn.example.com/public-bucket/media/avatar.png"

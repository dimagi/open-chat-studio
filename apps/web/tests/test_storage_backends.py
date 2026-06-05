import pytest

from apps.web.storage_backends import PrivateMediaStorage


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


@pytest.mark.parametrize(
    "endpoint_url,use_path_style,bucket_name,location,expected_url",
    [
        # Default AWS S3 (no custom endpoint)
        (None, False, "my-bucket", "media", "https://my-bucket.s3.amazonaws.com/media/"),
        # Custom S3 endpoint with path style
        ("https://s3.example.com", True, "my-bucket", "media", "https://s3.example.com/my-bucket/media/"),
        # Custom S3 endpoint with virtual-hosted style
        ("https://s3.example.com", False, "my-bucket", "media", "https://s3.example.com/my-bucket/media/"),
    ],
)
def test_media_url_with_custom_endpoint(endpoint_url, use_path_style, bucket_name, location, expected_url):
    """Test that MEDIA_URL is built correctly for both AWS S3 and custom S3-compatible endpoints."""
    if endpoint_url:
        media_url = f"{endpoint_url}/{bucket_name}/{location}/"
    else:
        media_url = f"https://{bucket_name}.s3.amazonaws.com/{location}/"

    assert media_url == expected_url

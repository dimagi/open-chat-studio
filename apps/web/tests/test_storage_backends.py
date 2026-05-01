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

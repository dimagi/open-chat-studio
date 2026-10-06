"""Shared helpers for tests that send files through a channel sender."""

from io import BytesIO
from unittest.mock import Mock

from apps.files.models import File


def make_mock_file(name, content_type, size, file_data=b"filedata"):
    file = Mock(spec=File)
    file.name = name
    file.content_type = content_type
    file.content_size = size
    file.file = BytesIO(file_data)
    file.read_bytes.return_value = file_data
    file.download_link.return_value = f"http://example.com/{name}"
    return file

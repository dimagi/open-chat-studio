import pytest

from apps.documents.models import CollectionFile
from apps.files.models import File
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.files import FileFactory


def _make_file(content_type: str, content_size: int) -> File:
    """Create a File and force content_size (bypassing the save() override that uses actual file size)."""
    file: File = FileFactory(content_type=content_type)  # ty: ignore[invalid-assignment]
    File.objects.filter(pk=file.pk).update(content_size=content_size)
    file.refresh_from_db()
    return file


@pytest.mark.django_db()
class TestUpdateSupportedChannels:
    """Tests for CollectionFile.update_supported_channels()."""

    def test_small_image_supported_everywhere(self):
        """A 1MB JPEG is sendable on all channels — empty dict."""
        file = _make_file("image/jpeg", 1 * 1024 * 1024)
        collection = CollectionFactory(is_index=False)
        cf = CollectionFile(file=file, collection=collection)
        cf.update_supported_channels()
        assert cf.supported_channels == {}

    def test_large_image_unsupported_on_whatsapp(self):
        """A 6MB image exceeds WhatsApp's 5MB limit but is fine for Telegram (10MB) and Slack (50MB)."""
        file = _make_file("image/png", 6 * 1024 * 1024)
        collection = CollectionFactory(is_index=False)
        cf = CollectionFile(file=file, collection=collection)
        cf.update_supported_channels()
        assert "whatsapp" in cf.supported_channels
        assert "telegram" not in cf.supported_channels
        assert "slack" not in cf.supported_channels
        assert cf.supported_channels["whatsapp"]["reason"]

    def test_very_large_image_unsupported_on_whatsapp_and_telegram(self):
        """An 11MB image exceeds both WhatsApp (5MB) and Telegram (10MB) limits."""
        file = _make_file("image/jpeg", 11 * 1024 * 1024)
        collection = CollectionFactory(is_index=False)
        cf = CollectionFile(file=file, collection=collection)
        cf.update_supported_channels()
        assert "whatsapp" in cf.supported_channels
        assert "telegram" in cf.supported_channels
        assert "slack" not in cf.supported_channels

    def test_unsupported_mime_type(self):
        """A text/plain file is unsupported on all channels."""
        file = _make_file("text/plain", 1024)
        collection = CollectionFactory(is_index=False)
        cf = CollectionFile(file=file, collection=collection)
        cf.update_supported_channels()
        assert "whatsapp" in cf.supported_channels
        assert "telegram" in cf.supported_channels
        assert "slack" in cf.supported_channels

    def test_reason_format(self):
        """Reason strings should be non-empty for unsupported channels."""
        file = _make_file("image/jpeg", 6 * 1024 * 1024)
        collection = CollectionFactory(is_index=False)
        cf = CollectionFile(file=file, collection=collection)
        cf.update_supported_channels()
        reason = cf.supported_channels["whatsapp"]["reason"]
        assert isinstance(reason, str)
        assert len(reason) > 0

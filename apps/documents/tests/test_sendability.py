from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse

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
        assert cf.unsupported_channels == {}

    def test_large_image_unsupported_on_whatsapp(self):
        """A 6MB image exceeds WhatsApp's 5MB limit but is fine for Telegram (10MB) and Slack (50MB)."""
        file = _make_file("image/png", 6 * 1024 * 1024)
        collection = CollectionFactory(is_index=False)
        cf = CollectionFile(file=file, collection=collection)
        cf.update_supported_channels()
        assert "whatsapp" in cf.unsupported_channels
        assert "telegram" not in cf.unsupported_channels
        assert "slack" not in cf.unsupported_channels
        assert cf.unsupported_channels["whatsapp"]["reason"]

    def test_very_large_image_unsupported_on_whatsapp_and_telegram(self):
        """An 11MB image exceeds both WhatsApp (5MB) and Telegram (10MB) limits."""
        file = _make_file("image/jpeg", 11 * 1024 * 1024)
        collection = CollectionFactory(is_index=False)
        cf = CollectionFile(file=file, collection=collection)
        cf.update_supported_channels()
        assert "whatsapp" in cf.unsupported_channels
        assert "telegram" in cf.unsupported_channels
        assert "slack" not in cf.unsupported_channels

    def test_unsupported_mime_type(self):
        """A text/plain file is unsupported on all channels."""
        file = _make_file("text/plain", 1024)
        collection = CollectionFactory(is_index=False)
        cf = CollectionFile(file=file, collection=collection)
        cf.update_supported_channels()
        assert "whatsapp" in cf.unsupported_channels
        assert "telegram" in cf.unsupported_channels
        assert "slack" in cf.unsupported_channels

    def test_reason_format(self):
        """Reason strings should be non-empty for unsupported channels."""
        file = _make_file("image/jpeg", 6 * 1024 * 1024)
        collection = CollectionFactory(is_index=False)
        cf = CollectionFile(file=file, collection=collection)
        cf.update_supported_channels()
        reason = cf.unsupported_channels["whatsapp"]["reason"]
        assert isinstance(reason, str)
        assert len(reason) > 0


@pytest.mark.django_db()
class TestPopulateSupportedChannelsCommand:
    """Tests for the populate_supported_channels management command."""

    def _call_command(self, *args, **kwargs):
        out = StringIO()
        call_command("populate_supported_channels", *args, stdout=out, **kwargs)
        return out.getvalue()

    def test_no_args_raises_error(self):
        with pytest.raises(CommandError):
            self._call_command()

    def test_collection_id_processes_media_collection(self):
        collection = CollectionFactory(is_index=False)
        file = _make_file("image/jpeg", 6 * 1024 * 1024)
        cf = CollectionFile.objects.create(file=file, collection=collection)
        assert cf.unsupported_channels == {}

        output = self._call_command("--collection-id", str(collection.id))

        cf.refresh_from_db()
        assert "whatsapp" in cf.unsupported_channels
        assert "1 files processed" in output

    def test_collection_id_skips_indexed_collection(self):
        collection = CollectionFactory(is_index=True)
        file = _make_file("image/jpeg", 6 * 1024 * 1024)
        CollectionFile.objects.create(file=file, collection=collection)

        output = self._call_command("--collection-id", str(collection.id))
        assert "0 files processed" in output

    def test_nonexistent_collection_id_raises_error(self):
        with pytest.raises(CommandError):
            self._call_command("--collection-id", "99999")

    def test_team_slug_processes_only_media_collections(self):
        collection_media = CollectionFactory(is_index=False)
        collection_index = CollectionFactory(
            is_index=True,
            team=collection_media.team,
            llm_provider=collection_media.llm_provider,
            embedding_provider_model=collection_media.embedding_provider_model,
        )
        file = _make_file("image/jpeg", 6 * 1024 * 1024)
        cf_media = CollectionFile.objects.create(file=file, collection=collection_media)
        cf_index = CollectionFile.objects.create(file=file, collection=collection_index)

        team_slug = collection_media.team.slug
        output = self._call_command("--team", team_slug)

        cf_media.refresh_from_db()
        cf_index.refresh_from_db()
        assert "whatsapp" in cf_media.unsupported_channels
        assert cf_index.unsupported_channels == {}
        assert "1 files processed" in output

    def test_dry_run_does_not_write(self):
        collection = CollectionFactory(is_index=False)
        file = _make_file("image/jpeg", 6 * 1024 * 1024)
        cf = CollectionFile.objects.create(file=file, collection=collection)

        output = self._call_command("--collection-id", str(collection.id), "--dry-run")

        cf.refresh_from_db()
        assert cf.unsupported_channels == {}
        assert "dry run" in output.lower()

    def test_idempotent(self):
        collection = CollectionFactory(is_index=False)
        file = _make_file("image/jpeg", 6 * 1024 * 1024)
        cf = CollectionFile.objects.create(file=file, collection=collection)

        self._call_command("--collection-id", str(collection.id))
        cf.refresh_from_db()
        first_result = cf.unsupported_channels.copy()

        self._call_command("--collection-id", str(collection.id))
        cf.refresh_from_db()
        assert cf.unsupported_channels == first_result


@pytest.mark.django_db()
class TestCollectionSendabilityUI:
    """Tests for the sendability warning banner on the collection home view."""

    def test_banner_shows_when_files_have_unsupported_channels(self, team_with_users, client):
        """The warning banner appears when a non-indexed collection has files with unsupported channels."""
        collection = CollectionFactory(team=team_with_users, is_index=False)
        file = _make_file("image/jpeg", 6 * 1024 * 1024)  # 6MB – exceeds WhatsApp limit
        cf = CollectionFile.objects.create(file=file, collection=collection)
        cf.update_supported_channels()
        cf.save()

        client.force_login(team_with_users.members.first())
        url = reverse("documents:single_collection_home", args=[team_with_users.slug, collection.pk])
        response = client.get(url)

        assert response.status_code == 200
        assert b"Some files in this collection cannot be sent directly" in response.content

    def test_banner_hidden_when_all_files_sendable(self, team_with_users, client):
        """No warning banner when every file is sendable on all channels."""
        collection = CollectionFactory(team=team_with_users, is_index=False)
        file = _make_file("image/jpeg", 1 * 1024 * 1024)  # 1MB – fine everywhere
        cf = CollectionFile.objects.create(file=file, collection=collection)
        cf.update_supported_channels()
        cf.save()

        client.force_login(team_with_users.members.first())
        url = reverse("documents:single_collection_home", args=[team_with_users.slug, collection.pk])
        response = client.get(url)

        assert response.status_code == 200
        assert b"Some files in this collection cannot be sent directly" not in response.content

    def test_banner_hidden_for_indexed_collection(self, team_with_users, client):
        """Indexed collections never show the sendability banner, even with large files."""
        collection = CollectionFactory(team=team_with_users, is_index=True)
        file = _make_file("image/jpeg", 6 * 1024 * 1024)
        # Manually set unsupported_channels to simulate an unsendable file
        CollectionFile.objects.create(
            file=file, collection=collection, unsupported_channels={"whatsapp": {"reason": "too large"}}
        )

        client.force_login(team_with_users.members.first())
        url = reverse("documents:single_collection_home", args=[team_with_users.slug, collection.pk])
        response = client.get(url)

        assert response.status_code == 200
        assert b"Some files in this collection cannot be sent directly" not in response.content

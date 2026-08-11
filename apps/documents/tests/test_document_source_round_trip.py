"""End-to-end guard: a synced document survives as the file the source served.

The unit tests around the sync stub the loader out, so nothing else covers the whole path
a real PDF takes: fetched from a feed, stored, read back for indexing, and downloaded by a
user. The download is what motivated storing the bytes verbatim — extracted text under the
source's own filename produced a `.pdf` no reader could open.
"""

import json
import pathlib

import pytest
from django.urls import reverse

from apps.documents.datamodels import DocumentSourceConfig, JSONCollectionSourceConfig
from apps.documents.document_source_service import DocumentSourceManager
from apps.documents.models import CollectionFile, DocumentSource, SourceType
from apps.documents.source_loaders.json_collection import JSONCollectionLoader
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.team import TeamWithUsersFactory

PDF_BYTES = (pathlib.Path(__file__).parent / "data" / "test.pdf").read_bytes()
PDF_TEXT = "PDF documents can be\nhard to read \U0001fae0\n"
FEED_URL = "https://example.com/collections.json"
PDF_URL = "https://example.com/docs/annual%20report.pdf"


@pytest.fixture()
def source(db):
    team = TeamWithUsersFactory.create()
    collection = CollectionFactory.create(team=team, is_index=True, is_remote_index=False)
    return DocumentSource.objects.create(
        collection=collection,
        team=team,
        source_type=SourceType.JSON_COLLECTION,
        config=DocumentSourceConfig(json_collection=JSONCollectionSourceConfig(json_url=FEED_URL)),
    )


@pytest.fixture()
def synced_file(source, monkeypatch):
    """Sync one PDF attachment from a feed, faking only the socket."""
    feed = [
        {
            "title": "Annual Report",
            "URI": "https://example.com/items/1",
            "date": "01/01/2026",
            "attachments": [{"file_type": "pdf", "title": "the report", "link": PDF_URL}],
        }
    ]

    def fake_read(self, url):
        return json.dumps(feed).encode() if url == FEED_URL else PDF_BYTES

    monkeypatch.setattr(JSONCollectionLoader, "_read_with_size_limit", fake_read)

    manager = DocumentSourceManager(source)
    monkeypatch.setattr(manager, "_index_files", lambda file_ids: None)
    result = manager.sync_collection()

    assert result.success, result.error_message
    assert result.files_added == 1
    return CollectionFile.objects.get(collection=source.collection).file


def test_sync_stores_the_pdf_the_source_served(synced_file):
    assert synced_file.file.read() == PDF_BYTES
    assert synced_file.content_type == "application/pdf"
    assert synced_file.content_size == len(PDF_BYTES)
    assert synced_file.name == "annual report.pdf"  # percent-decoded, extension intact


def test_text_is_extracted_when_the_stored_file_is_read(synced_file):
    """Indexing reads through the reader stack, so a stored PDF still yields its text."""
    assert synced_file.read_content() == PDF_TEXT


def test_downloaded_file_is_a_usable_pdf(client, source, synced_file):
    client.force_login(source.team.members.first())

    response = client.get(reverse("files:base", args=[source.team.slug, synced_file.id]))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"].endswith('.pdf"'), response["Content-Disposition"]
    assert b"".join(response.streaming_content) == PDF_BYTES

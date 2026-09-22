from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.documents.models import CollectionFile, FileStatus
from apps.files.models import File, FilePurpose
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.team import TeamWithUsersFactory

FAQ_CSV = b"question,answer,language\nWhen is the clinic open?,08:00 to 16:00,en\n"


@pytest.fixture()
def team():
    return TeamWithUsersFactory.create()


@pytest.fixture()
def local_collection(team):
    return CollectionFactory.create(team=team, is_index=True, is_remote_index=False)


@pytest.fixture()
def logged_in_client(client, team):
    client.force_login(team.members.first())
    return client


def upload(name=b"faq.csv", data=FAQ_CSV):
    return SimpleUploadedFile(name.decode() if isinstance(name, bytes) else name, data, content_type="text/csv")


@pytest.mark.django_db()
class TestRowImportPreview:
    def test_shows_headers_sample_rows_and_column_checkboxes(self, logged_in_client, team, local_collection):
        url = reverse("documents:row_import_preview", args=[team.slug, local_collection.id])
        response = logged_in_client.post(url, {"file": upload()})

        assert response.status_code == 200
        body = response.content.decode()
        assert 'name="metadata_columns" value="language"' in body
        assert "question: When is the clinic open?" in body
        assert "1 rows" in body or "1 row" in body

    def test_parse_error_is_shown_in_the_fragment(self, logged_in_client, team, local_collection):
        url = reverse("documents:row_import_preview", args=[team.slug, local_collection.id])
        response = logged_in_client.post(url, {"file": upload(data=b"a,b\n1,2,3\n")})

        assert response.status_code == 200
        assert "Row 1 has 3 values but the header has 2" in response.content.decode()
        assert 'name="metadata_columns"' not in response.content.decode()

    def test_oversized_rows_are_listed(self, logged_in_client, team, local_collection, settings):
        settings.COLLECTION_ROW_IMPORT_MAX_ROW_TOKENS = 5
        url = reverse("documents:row_import_preview", args=[team.slug, local_collection.id])
        response = logged_in_client.post(url, {"file": upload()})

        assert "Row 1" in response.content.decode()
        assert "too long" in response.content.decode()

    def test_remote_index_is_rejected(self, logged_in_client, team):
        remote = CollectionFactory.create(team=team, is_index=True, is_remote_index=True)
        url = reverse("documents:row_import_preview", args=[team.slug, remote.id])
        response = logged_in_client.post(url, {"file": upload()})

        assert response.status_code == 400

    def test_another_teams_collection_is_not_found(self, logged_in_client, team):
        other = CollectionFactory.create(team=TeamWithUsersFactory.create(), is_index=True, is_remote_index=False)
        url = reverse("documents:row_import_preview", args=[team.slug, other.id])
        response = logged_in_client.post(url, {"file": upload()})

        assert response.status_code == 404


@pytest.mark.django_db()
class TestRowImport:
    def test_creates_the_file_and_queues_indexing(self, logged_in_client, team, local_collection):
        url = reverse("documents:row_import", args=[team.slug, local_collection.id])
        with mock.patch("apps.documents.tasks.index_collection_files_task.delay") as delay:
            response = logged_in_client.post(url, {"file": upload(), "metadata_columns": ["language"]})

        assert response.status_code == 302
        file = File.objects.get(name="faq.csv", team=team)
        assert file.purpose == FilePurpose.COLLECTION
        collection_file = CollectionFile.objects.get(collection=local_collection, file=file)
        assert collection_file.status == FileStatus.PENDING
        assert collection_file.metadata.chunking_strategy is None
        assert collection_file.metadata.row_import.metadata_columns == ["language"]
        delay.assert_called_once_with([collection_file.id])

    def test_unknown_metadata_column_is_rejected(self, logged_in_client, team, local_collection):
        url = reverse("documents:row_import", args=[team.slug, local_collection.id])
        response = logged_in_client.post(url, {"file": upload(), "metadata_columns": ["nope"]})

        assert response.status_code == 302
        assert not File.objects.filter(name="faq.csv", team=team).exists()

    def test_wrong_extension_is_rejected(self, logged_in_client, team, local_collection):
        url = reverse("documents:row_import", args=[team.slug, local_collection.id])
        response = logged_in_client.post(url, {"file": upload(name="faq.xlsx")})

        assert response.status_code == 302
        assert not File.objects.filter(team=team).exists()

    def test_oversized_row_blocks_the_import(self, logged_in_client, team, local_collection, settings):
        settings.COLLECTION_ROW_IMPORT_MAX_ROW_TOKENS = 5
        url = reverse("documents:row_import", args=[team.slug, local_collection.id])
        logged_in_client.post(url, {"file": upload()})

        assert not File.objects.filter(team=team).exists()

    def test_bulk_upload_still_rejects_csv(self, logged_in_client, team, local_collection):
        url = reverse("documents:add_collection_files", args=[team.slug, local_collection.id])
        logged_in_client.post(url, {"files": [upload()]})

        assert not File.objects.filter(team=team).exists()


@pytest.mark.django_db()
class TestRowImportModalOnCollectionPage:
    def test_local_index_page_renders_the_modal(self, logged_in_client, team, local_collection):
        url = reverse("documents:single_collection_home", args=[team.slug, local_collection.id])
        body = logged_in_client.get(url).content.decode()

        assert "importRowsModal.showModal()" in body
        assert 'id="importRowsModal"' in body
        assert 'accept=".csv,.tsv"' in body

    def test_remote_index_page_has_no_row_import(self, logged_in_client, team):
        remote = CollectionFactory.create(team=team, is_index=True, is_remote_index=True)
        url = reverse("documents:single_collection_home", args=[team.slug, remote.id])

        assert "importRowsModal" not in logged_in_client.get(url).content.decode()

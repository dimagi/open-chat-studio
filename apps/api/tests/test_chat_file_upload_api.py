import os

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.files.models import File
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.tests.clients import ApiTestClient


@pytest.fixture()
def api_client():
    return APIClient()


@pytest.fixture()
def authed_client(team_with_users):
    user = team_with_users.members.first()
    client = ApiTestClient(user, team_with_users)
    return client


@pytest.fixture()
def session(experiment):
    return ExperimentSessionFactory(experiment=experiment)


def _get_test_file_path(filename):
    """Get the absolute path to a test file"""
    current_dir = os.path.dirname(__file__)
    return os.path.join(current_dir, "files", filename)


def create_uploaded_file_from_fixture(filename):
    """Create an UploadedFile from a fixture file"""
    file_path = _get_test_file_path(filename)

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Test file not found: {file_path}")

    with open(file_path, "rb") as f:
        content = f.read()

    ext = os.path.splitext(filename)[1].lower()
    content_type_map = {
        ".txt": "text/plain",
        ".csv": "text/csv",
        ".pdf": "application/pdf",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".html": "text/html",
    }
    content_type = content_type_map.get(ext, "application/octet-stream")

    return SimpleUploadedFile(filename, content, content_type=content_type)


def create_test_file(filename, content, content_type="text/plain"):
    """Create a test uploaded file with specific content"""
    if isinstance(content, str):
        content = content.encode("utf-8")
    return SimpleUploadedFile(filename, content, content_type=content_type)


@pytest.mark.django_db()
class TestChatFileUploadAPI:
    """Test chat file upload API functionality using real fixture files"""

    def test_successful_single_file_upload(self, api_client, session):
        """Test successful upload of a single file via API"""
        url = reverse("api:chat:upload-file", kwargs={"session_id": session.external_id})
        test_file = create_uploaded_file_from_fixture("small_text.txt")
        response = api_client.post(url, {"files": test_file}, format="multipart")

        assert response.status_code == status.HTTP_201_CREATED
        response_data = response.json()

        assert "files" in response_data
        assert len(response_data["files"]) == 1

        uploaded_file = response_data["files"][0]
        assert "id" in uploaded_file
        assert uploaded_file["name"] == "small_text.txt"
        assert uploaded_file["content_type"] == "text/plain"

        # Verify actual file size matches the fixture file
        expected_size = os.path.getsize(_get_test_file_path("small_text.txt"))
        assert uploaded_file["size"] == expected_size

        file_obj = File.objects.get(id=uploaded_file["id"])
        assert file_obj.name == "small_text.txt"
        assert file_obj.team == session.team

    def test_successful_multiple_file_upload(self, api_client, session):
        """Test uploading multiple real files at once via API"""
        url = reverse("api:chat:upload-file", kwargs={"session_id": session.external_id})
        files = [
            create_uploaded_file_from_fixture("small_text.txt"),
            create_uploaded_file_from_fixture("data.csv"),
            create_uploaded_file_from_fixture("image.jpg"),
        ]
        response = api_client.post(url, {"files": files}, format="multipart")

        assert response.status_code == status.HTTP_201_CREATED
        response_data = response.json()

        assert len(response_data["files"]) == 3

        file_names = [f["name"] for f in response_data["files"]]
        assert "small_text.txt" in file_names
        assert "data.csv" in file_names
        assert "image.jpg" in file_names

    def test_file_upload_with_participant_metadata(self, api_client, session):
        """Test file upload includes participant metadata via API"""
        url = reverse("api:chat:upload-file", kwargs={"session_id": session.external_id})
        test_file = create_uploaded_file_from_fixture("small_text.txt")
        data = {"files": test_file, "participant_remote_id": "user123", "participant_name": "John Doe"}
        response = api_client.post(url, data, format="multipart")

        assert response.status_code == status.HTTP_201_CREATED

        file_obj = File.objects.get(id=response.json()["files"][0]["id"])
        assert file_obj.metadata["participant_remote_id"] == "user123"
        assert file_obj.metadata["participant_name"] == "John Doe"
        assert file_obj.metadata["session_id"] == str(session.external_id)


@pytest.mark.django_db()
class TestFileValidationAPI:
    def test_file_size_limit_single_file(self, api_client, session):
        """Test API rejects files exceeding individual size limit (50MB)"""
        url = reverse("api:chat:upload-file", kwargs={"session_id": session.external_id})
        large_content = b"x" * (51 * 1024 * 1024)  # 51MB
        large_file = create_test_file("large.txt", large_content)
        response = api_client.post(url, {"files": large_file}, format="multipart")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "exceeds maximum size" in response.json()["error"]

    def test_total_file_size_limit(self, api_client, session):
        """Test API rejects uploads exceeding total size limit (50MB)"""
        url = reverse("api:chat:upload-file", kwargs={"session_id": session.external_id})
        files = [
            create_test_file("file1.txt", b"x" * (30 * 1024 * 1024)),  # 30MB
            create_test_file("file2.txt", b"x" * (25 * 1024 * 1024)),  # 25MB
        ]
        response = api_client.post(url, {"files": files}, format="multipart")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Total file size exceeds maximum" in response.json()["error"]

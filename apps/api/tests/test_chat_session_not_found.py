import uuid
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.test import APIClient

from apps.files.models import File
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.user import UserFactory

SESSION_ENDPOINTS = [
    pytest.param("send-message", "post", {}, id="send-message"),
    pytest.param("upload-file", "post", {}, id="upload-file"),
    pytest.param("poll-response", "get", {}, id="poll-response"),
    pytest.param("record-consent", "post", {}, id="record-consent"),
    pytest.param("task-poll-response", "get", {"task_id": "any-task"}, id="task-poll-response"),
]


@pytest.fixture()
def api_client():
    """An unauthenticated DRF test client."""
    return APIClient()


@pytest.mark.django_db()
@pytest.mark.parametrize("authenticated", [pytest.param(False, id="anonymous"), pytest.param(True, id="authenticated")])
@pytest.mark.parametrize(("url_name", "method", "extra_kwargs"), SESSION_ENDPOINTS)
def test_unknown_session_is_refused_with_403(api_client, authenticated, url_name, method, extra_kwargs):
    """Every session endpoint answers 403 for an unknown session id, for anonymous and signed-in callers."""
    if authenticated:
        api_client.force_authenticate(UserFactory.create())
    url = reverse(f"api:chat:{url_name}", kwargs={"session_id": uuid.uuid4(), **extra_kwargs})

    response = getattr(api_client, method)(url)

    assert response.status_code == 403


def _upload_payload():
    """Build a multipart payload holding one small text file."""
    return {"files": SimpleUploadedFile("note.txt", b"hello", content_type="text/plain")}


VIEW_GUARD_ENDPOINTS = [
    pytest.param("send-message", "post", lambda: {"message": "hi"}, "json", id="send-message"),
    pytest.param("upload-file", "post", _upload_payload, "multipart", id="upload-file"),
    pytest.param("poll-response", "get", lambda: None, None, id="poll-response"),
]


@pytest.mark.django_db()
@pytest.mark.parametrize(("url_name", "method", "make_payload", "request_format"), VIEW_GUARD_ENDPOINTS)
def test_session_the_view_cannot_load_is_a_404(api_client, experiment, url_name, method, make_payload, request_format):
    """A view that cannot load its session responds 404."""
    session = ExperimentSessionFactory.create(experiment=experiment, session_token_required=False)
    url = reverse(f"api:chat:{url_name}", kwargs={"session_id": session.external_id})
    format_kwargs = {"format": request_format} if request_format else {}

    with mock.patch("apps.api.views.chat.get_experiment_session_cached", return_value=None):
        response = getattr(api_client, method)(url, make_payload(), **format_kwargs)

    assert response.status_code == 404


@pytest.mark.django_db()
def test_upload_to_a_session_the_view_cannot_load_stores_no_file(api_client, experiment):
    """An upload to a session the view cannot load creates no File."""
    session = ExperimentSessionFactory.create(experiment=experiment, session_token_required=False)
    url = reverse("api:chat:upload-file", kwargs={"session_id": session.external_id})

    with mock.patch("apps.api.views.chat.get_experiment_session_cached", return_value=None):
        api_client.post(url, _upload_payload(), format="multipart")

    assert not File.objects.filter(team=experiment.team).exists()

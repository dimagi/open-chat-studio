import uuid

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

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
    return APIClient()


@pytest.mark.django_db()
@pytest.mark.parametrize("authenticated", [pytest.param(False, id="anonymous"), pytest.param(True, id="authenticated")])
@pytest.mark.parametrize(("url_name", "method", "extra_kwargs"), SESSION_ENDPOINTS)
def test_unknown_session_is_refused_with_403(api_client, authenticated, url_name, method, extra_kwargs):
    if authenticated:
        api_client.force_authenticate(UserFactory.create())
    url = reverse(f"api:chat:{url_name}", kwargs={"session_id": uuid.uuid4(), **extra_kwargs})

    response = getattr(api_client, method)(url)

    assert response.status_code == 403

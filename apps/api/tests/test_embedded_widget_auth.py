"""Tests for embedded widget DRF authentication and permissions."""

from unittest import mock

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.channels.models import ChannelPlatform
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.fixture()
def api_client():
    """Return an API client with session support enabled."""
    client = APIClient()
    client.session.save()
    return client


@pytest.fixture()
def embedded_widget_channel(experiment):
    """Create an embedded widget channel with a token and allowed domains."""
    return ExperimentChannelFactory(
        experiment=experiment,
        platform=ChannelPlatform.EMBEDDED_WIDGET,
        extra_data={
            "widget_token": "test_widget_token_123456789012",
            "allowed_domains": ["example.com", "*.test.com"],
        },
    )


@pytest.fixture()
def embedded_session(embedded_widget_channel):
    """Create a session associated with the embedded widget channel."""
    return ExperimentSessionFactory(
        experiment=embedded_widget_channel.experiment,
        experiment_channel=embedded_widget_channel,
    )


@pytest.mark.django_db()
def test_start_session_with_valid_embed_key(api_client, embedded_widget_channel):
    """Test starting a session with valid X-Embed-Key header - authentication succeeds."""
    url = reverse("api:chat:start-session")
    data = {
        "chatbot_id": embedded_widget_channel.experiment.public_id,
        "session_data": {"source": "widget"},
    }

    response = api_client.post(
        url,
        data=data,
        format="json",
        HTTP_X_EMBED_KEY="test_widget_token_123456789012",
    )

    assert response.status_code == 201
    response_json = response.json()
    assert "session_id" in response_json
    assert response_json["chatbot"]["id"] == str(embedded_widget_channel.experiment.public_id)


@pytest.mark.django_db()
def test_start_session_with_invalid_embed_key(api_client, embedded_widget_channel):
    """Test that invalid embed key is rejected by authentication."""
    url = reverse("api:chat:start-session")
    data = {
        "chatbot_id": embedded_widget_channel.experiment.public_id,
        "session_data": {"source": "widget"},
    }

    response = api_client.post(
        url,
        data=data,
        format="json",
        HTTP_X_EMBED_KEY="invalid_token_123456789012345",
    )

    # Should fail authentication
    assert response.status_code == 403
    assert "Embedded widget does not exist" in response.json()["detail"]


@pytest.mark.django_db()
def test_start_session_without_embed_key_missing_experiment_id(api_client, embedded_widget_channel):
    """Test that missing experiment_id causes authentication to fail when embed key is provided."""
    url = reverse("api:chat:start-session")
    data = {
        "session_data": {"source": "widget"},
        # Missing chatbot_id
    }

    response = api_client.post(
        url,
        data=data,
        format="json",
        HTTP_X_EMBED_KEY="test_widget_token_123456789012",
    )

    # Should fail with validation error (chatbot_id required)
    assert response.status_code == 400


@pytest.mark.django_db()
def test_send_message_with_valid_embed_key_and_domain(api_client, embedded_session):
    """Test sending message with valid X-Embed-Key and allowed domain."""
    # First, authenticate and store experiment_id in session
    api_client.session["auth_data"] = {"experiment_id": str(embedded_session.experiment.public_id)}
    api_client.session.save()

    url = reverse("api:chat:send-message", kwargs={"session_id": embedded_session.external_id})
    data = {"message": "Hello bot"}

    with mock.patch("apps.api.views.chat.get_response_for_webchat_task") as task:
        task.delay.return_value = mock.Mock(task_id="task123")
        response = api_client.post(
            url,
            data=data,
            format="json",
            HTTP_X_EMBED_KEY="test_widget_token_123456789012",
            HTTP_ORIGIN="https://example.com",
        )

    assert response.status_code == 202
    response_json = response.json()
    assert response_json["task_id"] == "task123"
    assert response_json["status"] == "processing"


@pytest.mark.django_db()
def test_send_message_with_wildcard_domain(api_client, embedded_session):
    """Test sending message with subdomain matching wildcard pattern."""
    api_client.session["auth_data"] = {"experiment_id": str(embedded_session.experiment.public_id)}
    api_client.session.save()

    url = reverse("api:chat:send-message", kwargs={"session_id": embedded_session.external_id})
    data = {"message": "Hello bot"}

    with mock.patch("apps.api.views.chat.get_response_for_webchat_task") as task:
        task.delay.return_value = mock.Mock(task_id="task123")
        response = api_client.post(
            url,
            data=data,
            format="json",
            HTTP_X_EMBED_KEY="test_widget_token_123456789012",
            HTTP_ORIGIN="https://subdomain.test.com",
        )

    assert response.status_code == 202


@pytest.mark.django_db()
def test_send_message_with_invalid_embed_key(api_client, embedded_session):
    """Test that invalid embed key is rejected by authentication."""
    api_client.session["auth_data"] = {"experiment_id": str(embedded_session.experiment.public_id)}
    api_client.session.save()

    url = reverse("api:chat:send-message", kwargs={"session_id": embedded_session.external_id})
    data = {"message": "Hello bot"}

    response = api_client.post(
        url,
        data=data,
        format="json",
        HTTP_X_EMBED_KEY="invalid_token",
        HTTP_ORIGIN="https://example.com",
    )

    # Should fail authentication
    assert response.status_code == 403


@pytest.mark.django_db()
def test_send_message_with_unauthorized_domain(api_client, embedded_session):
    """Test that unauthorized domain is rejected by permission check."""
    api_client.session["auth_data"] = {"experiment_id": str(embedded_session.experiment.public_id)}
    api_client.session.save()

    url = reverse("api:chat:send-message", kwargs={"session_id": embedded_session.external_id})
    data = {"message": "Hello bot"}

    response = api_client.post(
        url,
        data=data,
        format="json",
        HTTP_X_EMBED_KEY="test_widget_token_123456789012",
        HTTP_ORIGIN="https://evil.com",
    )

    # Should fail permission check
    assert response.status_code == 403


@pytest.mark.django_db()
def test_send_message_without_origin_header(api_client, embedded_session):
    """Test that missing Origin/Referer header is rejected by permission check."""
    api_client.session["auth_data"] = {"experiment_id": str(embedded_session.experiment.public_id)}
    api_client.session.save()

    url = reverse("api:chat:send-message", kwargs={"session_id": embedded_session.external_id})
    data = {"message": "Hello bot"}

    response = api_client.post(
        url,
        data=data,
        format="json",
        HTTP_X_EMBED_KEY="test_widget_token_123456789012",
    )

    # Should fail permission check due to missing origin
    assert response.status_code == 403


@pytest.mark.django_db()
def test_poll_response_with_valid_embed_key_and_domain(api_client, embedded_session):
    """Test polling messages with valid X-Embed-Key and allowed domain."""
    api_client.session["auth_data"] = {"experiment_id": str(embedded_session.experiment.public_id)}
    api_client.session.save()

    url = reverse("api:chat:poll-response", kwargs={"session_id": embedded_session.external_id})

    response = api_client.get(
        url,
        HTTP_X_EMBED_KEY="test_widget_token_123456789012",
        HTTP_ORIGIN="https://example.com",
    )

    assert response.status_code == 200
    response_json = response.json()
    assert "messages" in response_json
    assert "session_status" in response_json


@pytest.mark.django_db()
def test_legacy_flow_without_embed_key(api_client, experiment):
    """Test that the legacy flow still works without X-Embed-Key header."""
    url = reverse("api:chat:start-session")
    data = {
        "chatbot_id": experiment.public_id,
        "session_data": {"source": "widget"},
    }

    # No X-Embed-Key header - should use legacy flow (SessionAuthentication or unauthenticated)
    response = api_client.post(url, data=data, format="json")

    assert response.status_code == 201
    response_json = response.json()
    assert "session_id" in response_json


# Tests for authentication behavior


@pytest.mark.django_db()
def test_authentication_returns_experiment_channel(api_client, embedded_widget_channel):
    """Test that EmbeddedWidgetAuthentication returns ExperimentChannel as request.auth."""
    url = reverse("api:chat:start-session")
    data = {
        "chatbot_id": embedded_widget_channel.experiment.public_id,
        "session_data": {"source": "widget"},
    }

    response = api_client.post(
        url,
        data=data,
        format="json",
        HTTP_X_EMBED_KEY="test_widget_token_123456789012",
    )

    assert response.status_code == 201
    # Verify session was created with the correct channel
    response_json = response.json()
    assert response_json["chatbot"]["id"] == str(embedded_widget_channel.experiment.public_id)


@pytest.mark.django_db()
def test_authentication_stores_experiment_id_in_session(api_client, embedded_widget_channel):
    """Test that authentication stores experiment_id in session for subsequent requests."""
    url = reverse("api:chat:start-session")
    data = {
        "chatbot_id": embedded_widget_channel.experiment.public_id,
        "session_data": {"source": "widget"},
    }

    response = api_client.post(
        url,
        data=data,
        format="json",
        HTTP_X_EMBED_KEY="test_widget_token_123456789012",
    )

    assert response.status_code == 201
    # Verify session data was stored
    assert "auth_data" in api_client.session
    assert api_client.session["auth_data"]["experiment_id"] == str(embedded_widget_channel.experiment.public_id)

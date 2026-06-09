"""Tests for widget version recording and RFC 8594 sunset headers on the chat API."""

from datetime import UTC, datetime
from unittest import mock
from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.api.session_tokens import issue_session_token
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.widget_versions import WidgetDeprecation
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentSessionFactory

DEPRECATION = WidgetDeprecation(below_version="0.6.0", sunset_at=datetime(2026, 9, 1, tzinfo=UTC))


@pytest.fixture()
def api_client():
    return APIClient()


@pytest.fixture()
def widget_channel(experiment):
    return ExperimentChannelFactory.create(
        experiment=experiment,
        platform=ChannelPlatform.EMBEDDED_WIDGET,
        extra_data={
            "widget_token": "test_widget_token_123456789012",
            "allowed_domains": ["example.com"],
        },
    )


def _start_session(api_client, widget_channel, **extra):
    url = reverse("api:chat:start-session")
    data = {"chatbot_id": widget_channel.experiment.public_id}
    return api_client.post(
        url,
        data=data,
        format="json",
        HTTP_X_EMBED_KEY="test_widget_token_123456789012",
        HTTP_ORIGIN="https://example.com",
        **extra,
    )


@pytest.mark.django_db()
def test_start_session_records_widget_version(api_client, widget_channel):
    response = _start_session(api_client, widget_channel, HTTP_X_OCS_WIDGET_VERSION="0.8.0")
    assert response.status_code == 201
    widget_channel.refresh_from_db()
    assert widget_channel.widget_version == "0.8.0"
    assert widget_channel.widget_version_updated_at is not None


@pytest.mark.django_db()
def test_start_session_without_header_records_nothing(api_client, widget_channel):
    response = _start_session(api_client, widget_channel)
    assert response.status_code == 201
    widget_channel.refresh_from_db()
    assert widget_channel.widget_version is None


@pytest.mark.django_db()
def test_anonymous_api_fallback_does_not_record(api_client, experiment, team_with_users):
    # No X-Embed-Key: legacy flow falls back to the team API channel — must not write
    url = reverse("api:chat:start-session")
    response = api_client.post(
        url,
        data={"chatbot_id": experiment.public_id},
        format="json",
        HTTP_X_OCS_WIDGET_VERSION="0.8.0",
    )
    assert response.status_code == 201
    api_channel = ExperimentChannel.objects.get_team_api_channel(experiment.team)
    assert api_channel.widget_version is None


@pytest.mark.django_db()
def test_sunset_headers_for_deprecated_version(api_client, widget_channel):
    with patch("apps.channels.widget_versions.DEPRECATIONS", [DEPRECATION]):
        response = _start_session(api_client, widget_channel, HTTP_X_OCS_WIDGET_VERSION="0.5.0")
    assert response.status_code == 201
    assert response.headers["Deprecation"] == "true"
    assert "Sunset" in response.headers
    assert 'rel="successor-version"' in response.headers["Link"]


@pytest.mark.django_db()
def test_no_sunset_headers_for_current_version(api_client, widget_channel):
    with patch("apps.channels.widget_versions.DEPRECATIONS", [DEPRECATION]):
        response = _start_session(api_client, widget_channel, HTTP_X_OCS_WIDGET_VERSION="0.8.0")
    assert response.status_code == 201
    assert "Deprecation" not in response.headers


@pytest.mark.django_db()
def test_no_sunset_headers_without_version_header(api_client, widget_channel):
    # No x-ocs-widget-version header at all (curl, authed users): no deprecation headers
    with patch("apps.channels.widget_versions.DEPRECATIONS", [DEPRECATION]):
        response = _start_session(api_client, widget_channel)
    assert response.status_code == 201
    assert "Deprecation" not in response.headers


@pytest.mark.django_db()
def test_send_message_sunset_headers_for_deprecated_version(api_client, widget_channel):
    session = ExperimentSessionFactory.create(
        experiment=widget_channel.experiment,
        experiment_channel=widget_channel,
    )
    url = reverse("api:chat:send-message", kwargs={"session_id": session.external_id})
    with patch("apps.channels.widget_versions.DEPRECATIONS", [DEPRECATION]):
        with mock.patch("apps.api.views.chat.get_response_for_webchat_task") as task:
            task.delay.return_value = mock.Mock(task_id="123")
            response = api_client.post(
                url,
                data={"message": "hi"},
                format="json",
                HTTP_X_EMBED_KEY="test_widget_token_123456789012",
                HTTP_ORIGIN="https://example.com",
                HTTP_X_OCS_WIDGET_VERSION="0.5.0",
                HTTP_X_SESSION_TOKEN=issue_session_token(session),
            )
    assert response.status_code == 202
    assert response.headers["Deprecation"] == "true"
    assert "Sunset" in response.headers

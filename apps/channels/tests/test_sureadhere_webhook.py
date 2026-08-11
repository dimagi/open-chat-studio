import json
from unittest.mock import patch

import pytest
from django.http.request import HttpHeaders
from django.urls import reverse

from apps.channels import sureadhere_webhook
from apps.channels.models import ChannelPlatform
from apps.channels.tests.message_examples import sureadhere_messages
from apps.utils.factories.channels import ExperimentChannelFactory

WEBHOOK_SECRET = "sureadhere_test_webhook_secret"
TENANT_ID = "12"


def _headers(**wsgi_headers) -> HttpHeaders:
    """Build an HttpHeaders from WSGI-style keys, the way Django does for a request."""
    return HttpHeaders(wsgi_headers)


class TestGetPresentedSecret:
    """Which requests are considered to present a credential at all."""

    def test_custom_header(self):
        assert sureadhere_webhook.get_presented_secret(_headers(HTTP_X_OCS_WEBHOOK_SECRET="abc")) == "abc"

    def test_header_lookup_is_case_insensitive(self):
        """HttpHeaders normalises casing, so the sender's choice of casing does not matter."""
        assert sureadhere_webhook.get_presented_secret(HttpHeaders({"HTTP_X_ocs_Webhook_Secret": "abc"})) == "abc"

    def test_authorization_bearer(self):
        assert sureadhere_webhook.get_presented_secret(_headers(HTTP_AUTHORIZATION="Bearer abc")) == "abc"

    def test_bearer_scheme_is_case_insensitive(self):
        assert sureadhere_webhook.get_presented_secret(_headers(HTTP_AUTHORIZATION="bEaReR abc")) == "abc"

    def test_custom_header_wins_over_authorization(self):
        headers = _headers(HTTP_X_OCS_WEBHOOK_SECRET="abc", HTTP_AUTHORIZATION="Bearer xyz")
        assert sureadhere_webhook.get_presented_secret(headers) == "abc"

    @pytest.mark.parametrize(
        "wsgi_headers",
        [
            pytest.param({}, id="no_headers"),
            pytest.param({"HTTP_X_OCS_WEBHOOK_SECRET": ""}, id="empty_custom_header"),
            pytest.param({"HTTP_X_OCS_WEBHOOK_SECRET": "   "}, id="whitespace_custom_header"),
            pytest.param({"HTTP_AUTHORIZATION": ""}, id="empty_authorization"),
            pytest.param({"HTTP_AUTHORIZATION": "Bearer"}, id="bearer_with_no_token"),
            pytest.param({"HTTP_AUTHORIZATION": "Bearer "}, id="bearer_with_empty_token"),
            pytest.param({"HTTP_AUTHORIZATION": WEBHOOK_SECRET}, id="bare_token_no_scheme"),
            pytest.param({"HTTP_AUTHORIZATION": f"Token {WEBHOOK_SECRET}"}, id="token_scheme"),
            pytest.param({"HTTP_AUTHORIZATION": f"Basic {WEBHOOK_SECRET}"}, id="basic_scheme"),
        ],
    )
    def test_no_credential_presented(self, wsgi_headers):
        assert sureadhere_webhook.get_presented_secret(HttpHeaders(wsgi_headers)) == ""


class TestVerifySecret:
    def test_matching_secret(self):
        assert sureadhere_webhook.verify_secret(WEBHOOK_SECRET, WEBHOOK_SECRET) is True

    @pytest.mark.parametrize(
        ("presented", "expected"),
        [
            pytest.param("wrong", WEBHOOK_SECRET, id="wrong_secret"),
            pytest.param(WEBHOOK_SECRET.upper(), WEBHOOK_SECRET, id="case_changed"),
            pytest.param(WEBHOOK_SECRET + "x", WEBHOOK_SECRET, id="longer_than_secret"),
            pytest.param(WEBHOOK_SECRET[:-1], WEBHOOK_SECRET, id="prefix_of_secret"),
            pytest.param("", WEBHOOK_SECRET, id="nothing_presented"),
            pytest.param(WEBHOOK_SECRET, "", id="nothing_configured"),
            pytest.param("", "", id="empty_vs_empty"),
        ],
    )
    def test_rejected(self, presented, expected):
        assert sureadhere_webhook.verify_secret(presented, expected) is False


def _post(client, secret: str | None = None, *, header: str = "HTTP_X_OCS_WEBHOOK_SECRET", body: bytes | None = None):
    """POST an inbound message to the SureAdhere webhook, omitting the credential when secret is None."""
    if body is None:
        body = json.dumps(sureadhere_messages.inbound_message()).encode()
    headers = {} if secret is None else {header: secret}
    return client.post(
        reverse("channels:new_sureadhere_message", kwargs={"sureadhere_tenant_id": TENANT_ID}),
        data=body,
        content_type="application/json",
        **headers,
    )


@pytest.fixture()
def sureadhere_channel(sureadhere_provider):
    return ExperimentChannelFactory.create(
        platform=ChannelPlatform.SUREADHERE,
        messaging_provider=sureadhere_provider,
        experiment__team=sureadhere_provider.team,
        extra_data={"sureadhere_tenant_id": TENANT_ID},
    )


@pytest.fixture()
def secured_sureadhere_channel(sureadhere_channel):
    """A SureAdhere channel whose provider has a webhook secret, so verification is enforced."""
    provider = sureadhere_channel.messaging_provider
    provider.config = {**provider.config, "webhook_secret": WEBHOOK_SECRET}
    provider.save()
    return sureadhere_channel


@pytest.mark.django_db()
class TestNewSureAdhereMessageAuthentication:
    @pytest.mark.parametrize(
        "header",
        [
            pytest.param("HTTP_X_OCS_WEBHOOK_SECRET", id="custom_header"),
            pytest.param("HTTP_AUTHORIZATION", id="authorization_bearer"),
        ],
    )
    @patch("apps.channels.tasks.handle_sureadhere_message")
    def test_correct_secret_is_accepted(self, task, header, client, secured_sureadhere_channel):
        secret = WEBHOOK_SECRET if header == "HTTP_X_OCS_WEBHOOK_SECRET" else f"Bearer {WEBHOOK_SECRET}"
        response = _post(client, secret, header=header)
        assert response.status_code == 200
        task.delay.assert_called_once()

    @pytest.mark.parametrize(
        ("header", "secret"),
        [
            pytest.param("HTTP_X_OCS_WEBHOOK_SECRET", None, id="no_credential"),
            pytest.param("HTTP_X_OCS_WEBHOOK_SECRET", "wrong-secret", id="wrong_secret"),
            pytest.param("HTTP_X_OCS_WEBHOOK_SECRET", "", id="empty_secret"),
            pytest.param("HTTP_X_OCS_WEBHOOK_SECRET", "   ", id="whitespace_secret"),
            pytest.param("HTTP_X_OCS_WEBHOOK_SECRET", WEBHOOK_SECRET.upper(), id="case_changed_secret"),
            pytest.param("HTTP_X_OCS_WEBHOOK_SECRET", WEBHOOK_SECRET + "x", id="longer_than_secret"),
            pytest.param("HTTP_AUTHORIZATION", f"Bearer {WEBHOOK_SECRET}x", id="bearer_wrong_secret"),
            pytest.param("HTTP_AUTHORIZATION", "Bearer", id="bearer_with_no_token"),
            pytest.param("HTTP_AUTHORIZATION", WEBHOOK_SECRET, id="bare_token_no_scheme"),
            pytest.param("HTTP_AUTHORIZATION", f"Token {WEBHOOK_SECRET}", id="wrong_auth_scheme"),
        ],
    )
    @patch("apps.channels.tasks.handle_sureadhere_message")
    def test_bad_credential_is_rejected(self, task, header, secret, client, secured_sureadhere_channel):
        response = _post(client, secret, header=header)
        assert response.status_code == 401
        task.delay.assert_not_called()

    @patch("apps.channels.tasks.handle_sureadhere_message")
    def test_secret_in_the_query_string_is_not_accepted(self, task, client, secured_sureadhere_channel):
        url = reverse("channels:new_sureadhere_message", kwargs={"sureadhere_tenant_id": TENANT_ID})
        response = client.post(
            f"{url}?webhook_secret={WEBHOOK_SECRET}",
            data=json.dumps(sureadhere_messages.inbound_message()).encode(),
            content_type="application/json",
        )
        assert response.status_code == 401
        task.delay.assert_not_called()

    @patch("apps.channels.tasks.handle_sureadhere_message")
    def test_rejected_before_the_body_is_parsed(self, task, client, secured_sureadhere_channel):
        """The guard runs before json.loads, so an unauthenticated request cannot reach the parser."""
        response = _post(client, body=b"not json")
        assert response.status_code == 401
        task.delay.assert_not_called()

    @patch("apps.channels.tasks.handle_sureadhere_message")
    def test_unknown_tenant_is_still_a_404(self, task, client, secured_sureadhere_channel):
        url = reverse("channels:new_sureadhere_message", kwargs={"sureadhere_tenant_id": "9999"})
        response = client.post(url, data=b"{}", content_type="application/json")
        assert response.status_code == 404
        task.delay.assert_not_called()


@pytest.mark.django_db()
class TestNewSureAdhereMessagePassThrough:
    """Providers with no secret configured yet keep working, unverified. See the summary in
    _sureadhere_request_is_authorised: enforcing on deploy day would drop live patient traffic
    for every existing SureAdhere provider."""

    @pytest.mark.parametrize(
        "stored_secret",
        [
            pytest.param("absent", id="key_absent_from_config"),
            pytest.param("", id="empty_string"),
            pytest.param("   ", id="whitespace_only"),
            pytest.param(None, id="none"),
        ],
    )
    @patch("apps.channels.tasks.handle_sureadhere_message")
    def test_unconfigured_provider_accepts_unauthenticated_webhooks(
        self, task, stored_secret, client, sureadhere_channel
    ):
        if stored_secret != "absent":
            provider = sureadhere_channel.messaging_provider
            provider.config = {**provider.config, "webhook_secret": stored_secret}
            provider.save()
        response = _post(client)
        assert response.status_code == 200
        task.delay.assert_called_once()

    @patch("apps.channels.tasks.handle_sureadhere_message")
    def test_channel_without_a_messaging_provider_is_unchanged(self, task, client):
        """messaging_provider is optional on a channel. Such a channel cannot deliver anything
        (the task only resolves channels with a SureAdhere provider) but the response is a 200,
        as it was before this guard existed."""
        ExperimentChannelFactory.create(
            platform=ChannelPlatform.SUREADHERE,
            messaging_provider=None,
            extra_data={"sureadhere_tenant_id": TENANT_ID},
        )
        response = _post(client)
        assert response.status_code == 200
        task.delay.assert_called_once()

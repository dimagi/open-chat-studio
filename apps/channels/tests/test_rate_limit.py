"""Rate limiting behaviour for the inbound channel webhooks.

Every route counts inside its view, once the delivery has resolved to an
`ExperimentChannel` and passed whatever signature check the provider offers. The
identity is the channel's primary key, which the caller does not supply, so a
delivery cannot be counted against a channel it does not belong to, and traffic
that never resolves to a channel is not counted at all.
"""

import hashlib
import hmac
import json
from unittest.mock import patch

import pytest
from django.conf import settings
from django.test import override_settings
from django.urls import reverse

from apps.channels.models import ChannelPlatform
from apps.channels.tests.message_examples import meta_cloud_api_messages
from apps.utils.factories.channels import ExperimentChannelFactory

# Overrides the channels scope alone, leaving every other scope at its configured rate.
# Mirrors the shipped scope's policy so these tests exercise what deployments run.
TINY_LIMITS = settings.RATE_LIMITS | {"channels": {"rate": "2/5m", "fail_open": True, "refuse": False}}

META_WEBHOOK_URL_NAME = "channels:new_meta_cloud_api_message"
TELEGRAM_SECRET = "telegram-secret"


@pytest.fixture()
def telegram_channel(db):
    return ExperimentChannelFactory.create(platform=ChannelPlatform.TELEGRAM)


def _post_telegram(client, external_id, token=TELEGRAM_SECRET):
    return client.post(
        reverse("channels:new_telegram_message", args=[external_id]),
        data="{}",
        content_type="application/json",
        headers={"x-telegram-bot-api-secret-token": token},
    )


def _post_meta(client, payload, app_secret="test_app_secret"):
    body = json.dumps(payload).encode()
    signature = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        reverse(META_WEBHOOK_URL_NAME),
        data=body,
        content_type="application/json",
        headers={"x-hub-signature-256": f"sha256={signature}"},
    )


def _meta_payload(*phone_number_ids):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "BIZ_ID",
                "changes": [
                    {"value": meta_cloud_api_messages.text_message_value(pid), "field": "messages"}
                    for pid in phone_number_ids
                ],
            }
        ],
    }


@pytest.fixture()
def rate_limit_logs(caplog):
    """The over-limit signal is logged at INFO, which is below the default capture level."""
    with caplog.at_level("INFO", logger="ocs.rate_limit"):
        yield caplog


def _would_block_records(logs):
    return [record for record in logs.records if record.message == "rate_limit.would_block"]


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, TELEGRAM_SECRET_TOKEN=TELEGRAM_SECRET)
@patch("apps.channels.tasks.handle_telegram_message.delay")
def test_delivery_counts_against_the_resolved_channel(mock_delay, client, telegram_channel):
    """A delivery the view accepts is counted, and repeat deliveries to one channel
    draw down the same allowance.
    """
    first = _post_telegram(client, telegram_channel.external_id)
    second = _post_telegram(client, telegram_channel.external_id)

    assert first.wsgi_request.rate_limit_result.remaining == 1
    assert second.wsgi_request.rate_limit_result.remaining == 0


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, TELEGRAM_SECRET_TOKEN=TELEGRAM_SECRET)
def test_delivery_with_an_invalid_secret_token_is_not_counted(client, telegram_channel):
    """Counting sits behind the secret-token check, so unauthenticated traffic cannot
    spend a real channel's allowance.
    """
    response = _post_telegram(client, telegram_channel.external_id, token="wrong")

    assert response.status_code == 400
    assert not hasattr(response.wsgi_request, "rate_limit_result")


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, TELEGRAM_SECRET_TOKEN=TELEGRAM_SECRET)
def test_delivery_for_an_unknown_channel_is_not_counted(client):
    """An identifier that resolves to nothing has no channel to bill, so a caller
    cannot create counter entries by varying it.
    """
    response = _post_telegram(client, "8b1f0c2e-0000-0000-0000-000000000009")

    assert response.status_code == 404
    assert not hasattr(response.wsgi_request, "rate_limit_result")


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS)
@patch("apps.channels.tasks.handle_meta_cloud_api_message.delay")
def test_channels_behind_one_provider_do_not_share_a_bucket(
    mock_delay, client, rate_limit_logs, meta_cloud_api_whatsapp_channel, meta_cloud_api_provider
):
    """Two teams reached through the same Meta app arrive from the same address, so
    keying on that address puts them in one bucket and lets a busy team exhaust a
    quiet one's allowance.

    Keying on the resolved channel separates them: taking one team past its limit
    leaves the other inside its own, and the single over-limit signal names the team
    that crossed rather than whichever delivery happened to arrive fourth.
    """
    other_team_channel = ExperimentChannelFactory.create(
        platform=ChannelPlatform.WHATSAPP,
        messaging_provider=meta_cloud_api_provider,
        extra_data={"number": "+15559999999", "phone_number_id": "99999"},
    )
    assert other_team_channel.team_id != meta_cloud_api_whatsapp_channel.team_id

    for _ in range(3):
        _post_meta(client, _meta_payload("12345"))
    _post_meta(client, _meta_payload("99999"))

    assert mock_delay.call_count == 4
    (record,) = _would_block_records(rate_limit_logs)
    assert record.team_id == meta_cloud_api_whatsapp_channel.team_id


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS)
@patch("apps.channels.tasks.handle_meta_cloud_api_message.delay")
def test_a_multi_number_payload_counts_each_number_against_its_own_channel(
    mock_delay, client, rate_limit_logs, meta_cloud_api_whatsapp_channel, meta_cloud_api_provider
):
    """One payload can carry values for several phone numbers, so counting once per
    delivery would bill a whole batch to whichever channel the request keyed on.
    Counting inside the dispatch loop draws down each channel's own allowance.
    """
    ExperimentChannelFactory.create(
        platform=ChannelPlatform.WHATSAPP,
        messaging_provider=meta_cloud_api_provider,
        extra_data={"number": "+15559999999", "phone_number_id": "99999"},
    )
    both = _meta_payload("12345", "99999")

    _post_meta(client, both)
    _post_meta(client, both)
    response = _post_meta(client, _meta_payload("12345"))

    assert mock_delay.call_count == 5
    # Two batches spent each channel's allowance of two; only the third delivery to
    # 12345 crosses, and 99999 is untouched by it.
    (record,) = _would_block_records(rate_limit_logs)
    assert record.team_id == meta_cloud_api_whatsapp_channel.team_id
    assert response.status_code == 200


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS)
def test_meta_delivery_with_an_invalid_signature_is_not_counted(client, meta_cloud_api_whatsapp_channel):
    """The app secret lives on the resolved channel's provider, so the signature check
    can only run after the lookup. Counting runs after both.
    """
    response = _post_meta(client, _meta_payload("12345"), app_secret="not-the-app-secret")

    assert response.status_code == 200
    assert not hasattr(response.wsgi_request, "rate_limit_result")


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True, TELEGRAM_SECRET_TOKEN=TELEGRAM_SECRET)
@patch("apps.channels.tasks.handle_telegram_message.delay")
def test_an_over_limit_delivery_is_still_dispatched(mock_delay, client, rate_limit_logs, telegram_channel):
    """The scope counts and never refuses, including once enforcement is switched on.
    The provider has been answered and will not send the message again, so refusing
    here would discard a participant's message rather than shed load.
    """
    for _ in range(3):
        response = _post_telegram(client, telegram_channel.external_id)

    assert response.status_code == 200
    assert mock_delay.call_count == 3
    assert len(_would_block_records(rate_limit_logs)) == 1


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS, TELEGRAM_SECRET_TOKEN=TELEGRAM_SECRET)
@patch("apps.channels.tasks.handle_telegram_message.delay")
def test_the_over_limit_signal_names_the_team(mock_delay, client, rate_limit_logs, telegram_channel):
    """Resolving the channel before counting is what puts a team on the log line, which
    is how an over-limit identity is attributed without logging the identity itself.
    """
    for _ in range(3):
        _post_telegram(client, telegram_channel.external_id)

    (record,) = _would_block_records(rate_limit_logs)
    assert record.team_id == telegram_channel.team_id
    assert record.scope == "channels"


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS)
@pytest.mark.parametrize(
    ("url_name", "url_args"),
    [
        pytest.param("channels:new_twilio_message", [], id="twilio"),
        pytest.param("channels:new_sureadhere_message", ["42"], id="sureadhere"),
        pytest.param("channels:new_turn_message", ["8b1f0c2e-0000-0000-0000-000000000004"], id="turn"),
        pytest.param("channels:new_telegram_message", ["8b1f0c2e-0000-0000-0000-000000000005"], id="telegram"),
        pytest.param("slack_global:events", [], id="slack"),
    ],
)
def test_post_only_webhooks_answer_other_methods_without_counting(client, url_name, url_args):
    """A GET to a public webhook URL is answered by the method check and spends nothing."""
    response = client.get(reverse(url_name, args=url_args))

    assert response.status_code == 405
    assert not hasattr(response.wsgi_request, "rate_limit_result")


@pytest.mark.django_db()
@override_settings(RATE_LIMITS=TINY_LIMITS)
def test_meta_webhook_verification_handshake_is_not_counted(client, meta_cloud_api_provider):
    """The handshake carries no message and resolves no channel, so nothing bills it."""
    response = client.get(
        reverse(META_WEBHOOK_URL_NAME),
        {"hub.mode": "subscribe", "hub.verify_token": "test_verify_token", "hub.challenge": "1337"},
    )

    assert response.status_code == 200
    assert response.content == b"1337"
    assert not hasattr(response.wsgi_request, "rate_limit_result")

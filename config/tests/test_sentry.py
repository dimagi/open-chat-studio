"""Tests for Sentry event scrubbing.

Guards against the regression in which the CommCare Connect per-participant encryption key was
sent to Sentry as a stack-frame local, and against credential headers (API keys, chat session
tokens, widget embed keys, webhook signatures) being stored verbatim on an event (see
config/sentry.py).
"""

import pytest

from config.sentry import get_event_scrubber


def _frame_vars_event(local_vars: dict) -> dict:
    """Minimal Sentry event carrying a single stack frame with the given locals."""
    return {
        "exception": {
            "values": [
                {
                    "stacktrace": {
                        "frames": [
                            {"function": "send_message_to_user", "vars": dict(local_vars)},
                        ]
                    }
                }
            ]
        }
    }


def _scrubbed_vars(local_vars: dict) -> dict:
    event = _frame_vars_event(local_vars)
    get_event_scrubber().scrub_event(event)
    return event["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]


def _scrubbed_headers(headers: dict) -> dict:
    """Headers as they survive the scrubber, keyed the way sentry_sdk keys them on an event.

    sentry_sdk builds ``request.headers`` from the WSGI environ, title-casing the hyphenated name
    (``HTTP_X_SESSION_TOKEN`` -> ``X-Session-Token``), so these are the real on-the-wire spellings.
    """
    event = {"request": {"headers": dict(headers)}}
    get_event_scrubber().scrub_event(event)
    return event["request"]["headers"]


@pytest.mark.parametrize(
    "var_name",
    [
        pytest.param("encryption_key", id="encryption_key"),
        pytest.param("encryption_key_bytes", id="encryption_key_bytes"),
        pytest.param("session_token", id="session_token"),
        pytest.param("embed_key", id="embed_key"),
        pytest.param("widget_token", id="widget_token"),
        pytest.param("hmac_secret", id="hmac_secret"),
        pytest.param("app_secret", id="app_secret"),
        pytest.param("signing_secret", id="signing_secret"),
        pytest.param("auth_token", id="auth_token"),
        pytest.param("secret_key", id="secret_key"),
        pytest.param("secret_key_bytes", id="secret_key_bytes"),
        pytest.param("access_token", id="access_token"),
        pytest.param("verify_token", id="verify_token"),
        pytest.param("password", id="default-denylist-still-applies"),
    ],
)
def test_sensitive_frame_locals_are_scrubbed(var_name):
    scrubbed = _scrubbed_vars({var_name: "super-secret-value"})
    assert scrubbed[var_name] != "super-secret-value"


def test_non_sensitive_frame_locals_are_preserved():
    scrubbed = _scrubbed_vars({"channel_id": "abc-123", "message": "hello"})
    assert scrubbed["channel_id"] == "abc-123"
    assert scrubbed["message"] == "hello"


def test_secret_nested_in_a_dict_is_scrubbed():
    scrubbed = _scrubbed_vars({"payload": {"encryption_key": "super-secret-value"}})
    assert scrubbed["payload"]["encryption_key"] != "super-secret-value"


@pytest.mark.parametrize(
    "header_name",
    [
        pytest.param("X-Api-Key", id="rest-api-key"),
        pytest.param("Authorization", id="bearer-api-key-or-oauth-token"),
        pytest.param("X-Session-Token", id="chat-session-token"),
        pytest.param("X-Embed-Key", id="widget-embed-key"),
        pytest.param("X-Mac-Digest", id="commcare-connect-hmac"),
        pytest.param("X-Ocs-Webhook-Secret", id="sureadhere-webhook-secret"),
        pytest.param("X-Telegram-Bot-Api-Secret-Token", id="telegram-webhook-secret"),
        pytest.param("X-Twilio-Signature", id="twilio-webhook-signature"),
        pytest.param("X-Turn-Hook-Signature", id="turn-webhook-signature"),
        pytest.param("X-Hub-Signature-256", id="meta-webhook-signature"),
        pytest.param("X-Slack-Signature", id="slack-webhook-signature"),
        pytest.param("X-Csrftoken", id="csrf-token"),
        pytest.param("Cookie", id="default-denylist-still-applies"),
    ],
)
def test_credential_headers_are_scrubbed(header_name):
    scrubbed = _scrubbed_headers({header_name: "super-secret-value"})
    assert scrubbed[header_name] != "super-secret-value"


def test_non_sensitive_headers_are_preserved():
    scrubbed = _scrubbed_headers({"Content-Type": "application/json", "X-Ocs-Widget-Version": "2"})
    assert scrubbed["Content-Type"] == "application/json"
    assert scrubbed["X-Ocs-Widget-Version"] == "2"

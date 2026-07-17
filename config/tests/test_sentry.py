"""Tests for Sentry event scrubbing.

Guards against the regression in which the CommCare Connect per-participant encryption key was
sent to Sentry as a stack-frame local (see config/sentry.py).
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


@pytest.mark.parametrize(
    "var_name",
    [
        pytest.param("encryption_key", id="encryption_key"),
        pytest.param("encryption_key_bytes", id="encryption_key_bytes"),
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

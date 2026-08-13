import base64
import hashlib
import hmac
import json
import logging

import pytest
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
from sentry_sdk.utils import current_stacktrace

from apps.api.permissions import verify_hmac

SHARED_SECRET = "connect-shared-secret-value"


@verify_hmac
def hmac_protected_view(request):
    return HttpResponse("ok")


class StacktraceCapture(logging.Handler):
    """Record what the Sentry logging integration would ship for each log record.

    Sentry builds an event from a log record and, because the SDK is initialised with
    ``attach_stacktrace=True``, attaches ``current_stacktrace(include_local_variables=True)``
    while the emitting frames are still live. Capturing it from a handler therefore sees exactly
    the frame locals that would leave the process.
    """

    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []
        self.stacktraces: list[dict] = []

    def emit(self, record):
        self.records.append(record)
        self.stacktraces.append(current_stacktrace(include_local_variables=True, include_source_context=False))


@pytest.fixture()
def sentry_frames():
    handler = StacktraceCapture()
    logger = logging.getLogger("ocs.api")
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)


def _make_request(body: bytes, digest_header: str | None):
    headers = {"X-Mac-Digest": digest_header} if digest_header is not None else {}
    return RequestFactory().post(
        "/api/commcare_connect/consent", body, content_type="application/json", headers=headers
    )


def _valid_digest(body: bytes) -> bytes:
    return base64.b64encode(hmac.new(SHARED_SECRET.encode(), body, hashlib.sha256).digest())


def _dump_locals_of_module_under_test(stacktrace: dict) -> str:
    """Serialise the locals of the ``apps.api.permissions`` frames only.

    The test harness legitimately holds the secret in its own frames; in production the frames
    above the decorator are Django's request handling, which never sees it.
    """
    frames = [frame for frame in stacktrace["frames"] if frame.get("module") == "apps.api.permissions"]
    assert frames, "no apps.api.permissions frames were captured"
    return json.dumps(frames, default=repr)


@pytest.mark.parametrize(
    ("digest_header", "configured_secret"),
    [
        pytest.param("AAAA", SHARED_SECRET, id="digest-mismatch"),
        pytest.param(None, SHARED_SECRET, id="missing-header"),
        pytest.param("AAAA", "", id="missing-secret"),
    ],
)
def test_rejected_request_does_not_expose_the_shared_secret(sentry_frames, digest_header, configured_secret):
    request = _make_request(json.dumps({"channel_id": "abc"}).encode(), digest_header)

    with override_settings(COMMCARE_CONNECT_SERVER_SECRET=configured_secret):
        response = hmac_protected_view(request)

    assert response.status_code == 401
    [record] = sentry_frames.records
    [stacktrace] = sentry_frames.stacktraces
    frame_dump = _dump_locals_of_module_under_test(stacktrace)
    # Sanity check that the dump really does hold the locals of the rejecting frame, so the
    # assertions below cannot pass vacuously.
    assert "_inner" in frame_dump
    assert "expected_digest" in frame_dump
    assert SHARED_SECRET not in frame_dump
    # Below Sentry's default ``event_level`` so a bad signature does not create an event at all,
    # and no bogus "NoneType: None" traceback from ``logger.exception`` outside an except block.
    assert record.levelno == logging.WARNING
    assert record.exc_info is None


def test_correct_digest_is_accepted():
    body = json.dumps({"channel_id": "abc"}).encode()
    request = _make_request(body, _valid_digest(body).decode())

    with override_settings(COMMCARE_CONNECT_SERVER_SECRET=SHARED_SECRET):
        response = hmac_protected_view(request)

    assert response.status_code == 200
    assert response.content == b"ok"

import base64
import json
import os
from datetime import UTC, datetime, timedelta
from io import BytesIO
from unittest import mock
from uuid import UUID, uuid4

import httpx
import pytest
import time_machine
from Crypto.Cipher import AES
from django.conf import settings
from django.core.files.uploadhandler import MemoryFileUploadHandler, TemporaryFileUploadHandler
from django.http.multipartparser import MultiPartParser
from django.test import override_settings
from tenacity import wait_none

from apps.channels.clients.connect_client import (
    ATTACHMENT_ENCRYPTION_OVERHEAD_BYTES,
    LEGACY_APP_MESSAGE,
    MAX_ATTACHMENT_BYTES,
    CommCareConnectClient,
    Message,
    OutgoingAttachment,
    encrypt_attachment,
    fits_attachment_limit,
)


@pytest.fixture()
def disable_retry_wait():
    """Tenacity sleeps between attempts. Skip the wait so timeout/retry tests stay fast.

    The @retry decorator binds the wait strategy at decoration time, so patching
    `wait_exponential` in the module namespace has no effect — patch the bound
    Retrying instance's `.wait` attribute directly instead.
    """
    with (
        mock.patch.object(CommCareConnectClient._send_fcm.retry, "wait", wait_none()),
        mock.patch.object(CommCareConnectClient._post_create_message.retry, "wait", wait_none()),
    ):
        yield


@pytest.fixture()
def connect_credentials(settings):
    settings.COMMCARE_CONNECT_SERVER_SECRET = "123"
    settings.COMMCARE_CONNECT_SERVER_ID = "123"


class TestConnectClientSendRetry:
    """send_message_to_user retries transient httpx errors and reuses the same message_id
    across retries so the server can dedupe if a previous attempt actually arrived."""

    _send_url = f"{settings.COMMCARE_CONNECT_SERVER_URL}/messaging/send_fcm/"

    @override_settings(COMMCARE_CONNECT_SERVER_SECRET="123", COMMCARE_CONNECT_SERVER_ID="123")
    def test_retries_on_read_timeout(self, httpx_mock, disable_retry_wait):
        httpx_mock.add_exception(httpx.ReadTimeout("read timed out"), url=self._send_url)
        httpx_mock.add_response(method="POST", url=self._send_url, status_code=200)

        client = CommCareConnectClient()
        client.send_message_to_user(str(uuid4()), message="hello", encryption_key=os.urandom(32))

        requests = httpx_mock.get_requests()
        assert len(requests) == 2

    @override_settings(COMMCARE_CONNECT_SERVER_SECRET="123", COMMCARE_CONNECT_SERVER_ID="123")
    def test_message_already_exists_treated_as_success(self, httpx_mock, disable_retry_wait):
        """A timeout followed by a 400 MESSAGE_ID_ALREADY_EXISTS means the message arrived; not an error."""
        httpx_mock.add_exception(httpx.ReadTimeout("read timed out"), url=self._send_url)
        httpx_mock.add_response(
            method="POST", url=self._send_url, json={"errors": "MESSAGE_ID_ALREADY_EXISTS"}, status_code=400
        )

        client = CommCareConnectClient()
        client.send_message_to_user(str(uuid4()), message="hello", encryption_key=os.urandom(32))

        assert len(httpx_mock.get_requests()) == 2

    @override_settings(COMMCARE_CONNECT_SERVER_SECRET="123", COMMCARE_CONNECT_SERVER_ID="123")
    def test_malformed_400_body_still_raises(self, httpx_mock, disable_retry_wait):
        """A 400 with an undecodable body must not be swallowed; it still raises via _raise_for_status."""
        httpx_mock.add_response(method="POST", url=self._send_url, content=b"\xff\xfe", status_code=400)

        client = CommCareConnectClient()
        with pytest.raises(httpx.HTTPStatusError):
            client.send_message_to_user(str(uuid4()), message="hello", encryption_key=os.urandom(32))

    @override_settings(COMMCARE_CONNECT_SERVER_SECRET="123", COMMCARE_CONNECT_SERVER_ID="123")
    def test_message_id_stable_across_retries(self, httpx_mock, disable_retry_wait):
        httpx_mock.add_exception(httpx.ReadTimeout("read timed out"), url=self._send_url)
        httpx_mock.add_response(method="POST", url=self._send_url, status_code=200)

        client = CommCareConnectClient()
        client.send_message_to_user(str(uuid4()), message="hello", encryption_key=os.urandom(32))

        requests = httpx_mock.get_requests()
        ids = [json.loads(r.read())["message_id"] for r in requests]
        assert ids[0] == ids[1], "message_id must be stable across retries for server-side dedupe"


class TestConnectClient:
    @override_settings(COMMCARE_CONNECT_SERVER_SECRET="123", COMMCARE_CONNECT_SERVER_ID="123")
    def test_encrypt_and_decrypt_message(self):
        encryption_key = os.urandom(32)
        connect_client = CommCareConnectClient()
        msg = "this is a secret message"
        result = connect_client._decrypt_message(encryption_key, *connect_client._encrypt_message(encryption_key, msg))
        assert result == msg

    @override_settings(COMMCARE_CONNECT_SERVER_SECRET="123", COMMCARE_CONNECT_SERVER_ID="123")
    def test_decrypt_messages(self):
        encryption_key = os.urandom(32)
        cipher = AES.new(encryption_key, mode=AES.MODE_GCM)
        ciphertext, tag = cipher.encrypt_and_digest(b"this is a secret message")

        connect_client = CommCareConnectClient()
        payload = Message(
            timestamp="2021-10-10T10:10:10Z",
            message_id=uuid4(),
            ciphertext=base64.b64encode(
                ciphertext,
            ).decode(),
            tag=base64.b64encode(tag).decode(),
            nonce=base64.b64encode(cipher.nonce).decode(),
        )
        messages = connect_client.decrypt_messages(encryption_key=encryption_key, messages=[payload])
        assert messages[0] == "this is a secret message"

    @override_settings(COMMCARE_CONNECT_SERVER_SECRET="123", COMMCARE_CONNECT_SERVER_ID="123")
    def test_send_message_error_attaches_response_body(self, httpx_mock):
        """A 400 from Connect attaches the response body as a note so it's visible in Sentry."""
        httpx_mock.add_response(
            method="POST",
            url=f"{settings.COMMCARE_CONNECT_SERVER_URL}/messaging/send_fcm/",
            json={"errors": "no_user_consent"},
            status_code=400,
        )

        connect_client = CommCareConnectClient()
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            connect_client.send_message_to_user(str(uuid4()), message="hi", encryption_key=os.urandom(32))

        assert any("no_user_consent" in note for note in exc_info.value.__notes__)

    @override_settings(COMMCARE_CONNECT_SERVER_SECRET="123", COMMCARE_CONNECT_SERVER_ID="123")
    def test_send_message_to_user(self, httpx_mock):
        httpx_mock.add_response(
            method="POST",
            url=f"{settings.COMMCARE_CONNECT_SERVER_URL}/messaging/send_fcm/",
            json={"message_id": "765aec754eacf3221"},
            status_code=200,
        )

        channel_id = str(uuid4())
        message = "Hi there human"
        encryption_key = os.urandom(32)

        connect_client = CommCareConnectClient()
        connect_client.send_message_to_user(channel_id, message=message, encryption_key=encryption_key)
        request = httpx_mock.get_request()
        assert request.headers["Authorization"].split(" ")[0] == "Basic"
        request_data = json.loads(request.read())
        message_content = request_data["content"]

        assert "content" in request_data
        assert "channel" in request_data
        assert "message_id" in request_data

        assert "nonce" in message_content
        assert "tag" in message_content
        assert "ciphertext" in message_content


_CREATE_MESSAGE_URL = f"{settings.COMMCARE_CONNECT_SERVER_URL}/messaging/create_message/"


def _parse_multipart(request: httpx.Request):
    """Parse a request body with Django's MultiPartParser, as PersonalID does. Returns (data, files)."""
    body = request.read()
    meta = {"CONTENT_TYPE": request.headers["Content-Type"], "CONTENT_LENGTH": str(len(body))}
    return MultiPartParser(meta, BytesIO(body), [MemoryFileUploadHandler(), TemporaryFileUploadHandler()]).parse()


def _sent_message(request: httpx.Request) -> dict:
    data, _files = _parse_multipart(request)
    return json.loads(data["message"])


def _decrypt_text(encryption_key: bytes, content: dict) -> str:
    cipher = AES.new(encryption_key, AES.MODE_GCM, nonce=base64.b64decode(content["nonce"]))
    return cipher.decrypt_and_verify(base64.b64decode(content["ciphertext"]), base64.b64decode(content["tag"])).decode()


def _decrypt_attachment(encryption_key: bytes, blob: bytes) -> bytes:
    """Decrypt the layout the app expects: 12-byte nonce, ciphertext, 16-byte tag."""
    nonce, ciphertext, tag = blob[:12], blob[12:-16], blob[-16:]
    return AES.new(encryption_key, AES.MODE_GCM, nonce=nonce).decrypt_and_verify(ciphertext, tag)


_SITE_MAP = OutgoingAttachment(name="site-map.jpg", content_type="image/jpeg", content=os.urandom(2048))
_INSTRUCTIONS = OutgoingAttachment(name="instructions.mp3", content_type="audio/mpeg", content=os.urandom(4096))


class TestEncryptAttachment:
    def test_round_trip(self):
        encryption_key = os.urandom(32)
        content = os.urandom(1000)

        blob = encrypt_attachment(encryption_key, content)

        assert _decrypt_attachment(encryption_key, blob) == content

    def test_adds_nonce_and_tag_overhead(self):
        blob = encrypt_attachment(os.urandom(32), b"x" * 1000)

        assert len(blob) == 1000 + 28
        assert ATTACHMENT_ENCRYPTION_OVERHEAD_BYTES == 28

    def test_uses_a_fresh_nonce_each_call(self):
        encryption_key = os.urandom(32)

        first = encrypt_attachment(encryption_key, b"same content")
        second = encrypt_attachment(encryption_key, b"same content")

        assert first[:12] != second[:12]


@pytest.mark.parametrize(
    ("content_size", "fits"),
    [
        pytest.param(MAX_ATTACHMENT_BYTES - 28, True, id="at-limit-after-encryption"),
        pytest.param(MAX_ATTACHMENT_BYTES - 27, False, id="one-byte-over-after-encryption"),
        pytest.param(1, True, id="one-byte"),
        pytest.param(0, False, id="empty"),
        pytest.param(None, False, id="unknown-size"),
    ],
)
def test_fits_attachment_limit(content_size, fits):
    assert fits_attachment_limit(content_size) is fits


def test_outgoing_attachment_repr_excludes_content():
    attachment = OutgoingAttachment(name="report.pdf", content_type="application/pdf", content=b"participant-details")

    assert "participant-details" not in repr(attachment)


@pytest.mark.usefixtures("connect_credentials")
class TestCreateMessage:
    @pytest.mark.parametrize(
        ("text", "attachments"),
        [
            pytest.param("Hello", [], id="text-only"),
            pytest.param("Hello", [_SITE_MAP, _INSTRUCTIONS], id="text-and-attachments"),
            pytest.param("", [_SITE_MAP], id="attachments-only"),
        ],
    )
    def test_sends_multipart_with_message_field_and_attachment_parts(self, httpx_mock, text, attachments):
        """PersonalID accepts only multipart, reads `message` as a form field, and requires the file
        parts to be exactly attachment_0, attachment_1, ..."""
        httpx_mock.add_response(method="POST", url=_CREATE_MESSAGE_URL, status_code=200)

        CommCareConnectClient().create_message(str(uuid4()), os.urandom(32), text, attachments)

        request = httpx_mock.get_request()
        assert request.headers["Content-Type"].startswith("multipart/form-data")
        assert request.headers["Authorization"].startswith("Basic ")
        data, files = _parse_multipart(request)
        assert list(data.keys()) == ["message"]
        expected_parts = [f"attachment_{index}" for index in range(len(attachments))]
        assert list(files.keys()) == expected_parts
        assert [files[part].name for part in expected_parts] == expected_parts

    def test_text_only_message(self, httpx_mock):
        httpx_mock.add_response(method="POST", url=_CREATE_MESSAGE_URL, status_code=200)
        channel_id = str(uuid4())
        encryption_key = os.urandom(32)

        CommCareConnectClient().create_message(channel_id, encryption_key, "Hi there human")

        message = _sent_message(httpx_mock.get_request())
        assert set(message) == {"channel", "message_id", "content"}
        assert message["channel"] == channel_id
        assert UUID(message["message_id"]).version == 4
        assert _decrypt_text(encryption_key, message["content"]) == "Hi there human"

    def test_message_with_attachments(self, httpx_mock):
        httpx_mock.add_response(method="POST", url=_CREATE_MESSAGE_URL, status_code=200)
        encryption_key = os.urandom(32)

        CommCareConnectClient().create_message(str(uuid4()), encryption_key, "See attached", [_SITE_MAP, _INSTRUCTIONS])

        request = httpx_mock.get_request()
        data, files = _parse_multipart(request)
        message = json.loads(data["message"])
        assert set(message) == {"channel", "message_id", "content", "content_legacy_msg", "expires_at", "attachments"}
        assert _decrypt_text(encryption_key, message["content"]) == "See attached"
        assert _decrypt_text(encryption_key, message["content_legacy_msg"]) == LEGACY_APP_MESSAGE
        assert message["attachments"] == [
            {"name": "site-map.jpg", "type": "image/jpeg", "size": len(_SITE_MAP.content) + 28},
            {"name": "instructions.mp3", "type": "audio/mpeg", "size": len(_INSTRUCTIONS.content) + 28},
        ]
        assert files["attachment_0"].size == message["attachments"][0]["size"]
        assert files["attachment_1"].size == message["attachments"][1]["size"]
        assert _decrypt_attachment(encryption_key, files["attachment_0"].read()) == _SITE_MAP.content
        assert _decrypt_attachment(encryption_key, files["attachment_1"].read()) == _INSTRUCTIONS.content

    def test_attachments_only_message_omits_content(self, httpx_mock):
        httpx_mock.add_response(method="POST", url=_CREATE_MESSAGE_URL, status_code=200)
        encryption_key = os.urandom(32)

        CommCareConnectClient().create_message(str(uuid4()), encryption_key, "", [_SITE_MAP])

        message = _sent_message(httpx_mock.get_request())
        assert "content" not in message
        assert _decrypt_text(encryption_key, message["content_legacy_msg"]) == LEGACY_APP_MESSAGE

    @time_machine.travel(datetime(2026, 10, 6, 12, 0, tzinfo=UTC), tick=False)
    def test_attachments_expire_after_ninety_days(self, httpx_mock):
        httpx_mock.add_response(method="POST", url=_CREATE_MESSAGE_URL, status_code=200)

        CommCareConnectClient().create_message(str(uuid4()), os.urandom(32), "", [_SITE_MAP])

        expires_at = datetime.fromisoformat(_sent_message(httpx_mock.get_request())["expires_at"])
        assert expires_at == datetime(2026, 10, 6, 12, 0, tzinfo=UTC) + timedelta(days=90)

    @pytest.mark.parametrize(
        ("attachments", "read_timeout"),
        [
            pytest.param([], 10, id="text-only"),
            pytest.param([_SITE_MAP], 60, id="with-attachments"),
        ],
    )
    def test_read_timeout_is_longer_for_uploads(self, httpx_mock, attachments, read_timeout):
        """PersonalID stores attachments before it replies; text-only sends keep the client default."""
        httpx_mock.add_response(method="POST", url=_CREATE_MESSAGE_URL, status_code=200)

        CommCareConnectClient().create_message(str(uuid4()), os.urandom(32), "Hello", attachments)

        assert httpx_mock.get_request().extensions["timeout"]["read"] == read_timeout


@pytest.mark.usefixtures("connect_credentials", "disable_retry_wait")
class TestCreateMessageRetry:
    """Network errors and timeouts are retried with the same message_id and the same encrypted
    bytes, so PersonalID can recognise a retry of a message that already arrived."""

    def test_retry_resends_the_same_message(self, httpx_mock):
        httpx_mock.add_exception(httpx.ReadTimeout("read timed out"), url=_CREATE_MESSAGE_URL)
        httpx_mock.add_response(method="POST", url=_CREATE_MESSAGE_URL, status_code=200)

        CommCareConnectClient().create_message(str(uuid4()), os.urandom(32), "Hello", [_SITE_MAP])

        first, second = (_parse_multipart(request) for request in httpx_mock.get_requests())
        assert first[0]["message"] == second[0]["message"]
        assert first[1]["attachment_0"].read() == second[1]["attachment_0"].read()

    def test_message_already_exists_treated_as_success(self, httpx_mock):
        """A timeout followed by MESSAGE_ID_ALREADY_EXISTS means the first attempt arrived."""
        httpx_mock.add_exception(httpx.ReadTimeout("read timed out"), url=_CREATE_MESSAGE_URL)
        httpx_mock.add_response(
            method="POST", url=_CREATE_MESSAGE_URL, json={"errors": "MESSAGE_ID_ALREADY_EXISTS"}, status_code=400
        )

        CommCareConnectClient().create_message(str(uuid4()), os.urandom(32), "Hello")

        assert len(httpx_mock.get_requests()) == 2

    @pytest.mark.parametrize(
        ("status_code", "error_code"),
        [
            pytest.param(403, "RICH_MESSAGING_DISABLED", id="rich-messaging-disabled"),
            pytest.param(400, "ATTACHMENT_TOO_LARGE", id="attachment-too-large"),
            pytest.param(413, "REQUEST_TOO_LARGE", id="request-too-large"),
        ],
    )
    def test_refusals_raise_with_body_and_are_not_retried(self, httpx_mock, status_code, error_code):
        httpx_mock.add_response(
            method="POST", url=_CREATE_MESSAGE_URL, json={"errors": error_code}, status_code=status_code
        )

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            CommCareConnectClient().create_message(str(uuid4()), os.urandom(32), "Hello", [_SITE_MAP])

        assert any(error_code in note for note in exc_info.value.__notes__)
        assert len(httpx_mock.get_requests()) == 1

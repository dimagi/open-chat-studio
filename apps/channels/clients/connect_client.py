import base64
import json
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TypedDict
from uuid import UUID, uuid4

import httpx
from Crypto.Cipher import AES
from django.conf import settings
from django.utils import timezone
from tenacity import before_sleep_log, retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger("ocs.channels.connect")

# Connect returns this in a 400 body when a message with the same message_id was already
# received. Because we reuse the message_id across retries, this happens when a prior attempt
# timed out client-side but actually arrived — the message is delivered, so it's not an error.
MESSAGE_ID_ALREADY_EXISTS = "MESSAGE_ID_ALREADY_EXISTS"

# PersonalID's attachment contract. Each attachment is sent as nonce + ciphertext + tag, and the
# limits count those encrypted bytes.
ATTACHMENT_NONCE_BYTES = 12
GCM_TAG_BYTES = 16
ATTACHMENT_ENCRYPTION_OVERHEAD_BYTES = ATTACHMENT_NONCE_BYTES + GCM_TAG_BYTES
MAX_ATTACHMENT_BYTES = 2_621_440  # 2.5 MiB
MAX_ATTACHMENTS_PER_MESSAGE = 10
MAX_MESSAGE_ATTACHMENT_BYTES = 15 * 1024 * 1024

# What app versions that cannot show attachments display instead of a message that has them
LEGACY_APP_MESSAGE = (
    "This message contains attachments but your version of the app is too old to see them."
    " Please update your Android app."
)
ATTACHMENT_EXPIRY = timedelta(days=90)

_CLIENT_TIMEOUT = 10
# PersonalID writes the attachments to storage before it replies
_ATTACHMENT_UPLOAD_TIMEOUT = httpx.Timeout(_CLIENT_TIMEOUT, read=60)


def fits_attachment_limit(content_size: int | None) -> bool:
    """Whether a file of this many bytes is within PersonalID's per-attachment limit once encrypted."""
    if not content_size or content_size <= 0:
        return False
    return content_size + ATTACHMENT_ENCRYPTION_OVERHEAD_BYTES <= MAX_ATTACHMENT_BYTES


def encrypt_attachment(encryption_key: bytes, content: bytes) -> bytes:
    """Encrypt a file into the layout the app expects: 12-byte nonce, ciphertext, 16-byte tag."""
    nonce = os.urandom(ATTACHMENT_NONCE_BYTES)
    cipher = AES.new(encryption_key, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(content)
    return nonce + ciphertext + tag


@dataclass(frozen=True)
class OutgoingAttachment:
    name: str
    content_type: str
    # Plaintext; kept out of the repr so it does not reach Sentry with the stack-frame locals
    content: bytes = field(repr=False)


def _raise_for_status(response: httpx.Response) -> None:
    """Raise on HTTP errors, attaching the response body so it surfaces in logs and Sentry.

    ``httpx.HTTPStatusError`` only stringifies the status line, so the CommCare Connect error
    body (e.g. ``{"errors": "no_user_consent"}``) would otherwise be lost. The note preserves the
    exception type and grouping while making the reason visible.
    """
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        e.add_note(f"Response body: {response.text}")
        raise


class Message(TypedDict):
    timestamp: str
    message_id: UUID
    ciphertext: str
    tag: str
    nonce: str


class NewMessagePayload(TypedDict):
    channel_id: UUID
    messages: list[Message]


class CommCareConnectClient:
    def __init__(self):
        if not all([settings.COMMCARE_CONNECT_SERVER_ID, settings.COMMCARE_CONNECT_SERVER_SECRET]):
            raise ValueError("COMMCARE_CONNECT_SERVER_ID and COMMCARE_CONNECT_SERVER_SECRET must be set")
        self._base_url = settings.COMMCARE_CONNECT_SERVER_URL
        self.client = httpx.Client(
            auth=httpx.BasicAuth(settings.COMMCARE_CONNECT_SERVER_ID, settings.COMMCARE_CONNECT_SERVER_SECRET),
            timeout=_CLIENT_TIMEOUT,
        )

    @retry(
        wait=wait_exponential(multiplier=1, min=1, max=5),
        reraise=True,
        retry=retry_if_exception_type((httpx.NetworkError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        before_sleep=before_sleep_log(logger, logging.INFO),
    )
    def create_channel(self, connect_id: str, channel_source: str, channel_name: str | None = None) -> dict:
        """Create (or fetch, idempotently) the Connect channel for a participant.

        ``channel_source`` is Connect's identity key for the channel (per participant) and must be
        a stable identifier; ``channel_name`` is the display name shown to the participant.
        """
        url = f"{self._base_url}/messaging/create_channel/"
        data = {"connectid": str(connect_id), "channel_source": channel_source}
        if channel_name:
            data["channel_name"] = channel_name
        response = self.client.post(url, json=data)
        _raise_for_status(response)
        return response.json()

    def decrypt_messages(self, encryption_key: bytes, messages: list[Message]) -> list[str]:
        """
        Decrypts the `MessagePayload` list using the provided key and verifies the message authenticity

        The key argument is named ``encryption_key`` so Sentry's ``EventScrubber`` denylist scrubs it
        from captured stack-frame locals; see ``SENTRY_DSN`` setup in ``config/settings.py``.

        Raises:
            ValueError if the message authenticity cannot be trusted
        """
        decrypted_messages = []
        for message in messages:
            message_text = self._decrypt_message(
                encryption_key, ciphertext=message["ciphertext"], tag=message["tag"], nonce=message["nonce"]
            )
            decrypted_messages.append(message_text)

        return decrypted_messages

    def send_message_to_user(self, channel_id: str, message: str, encryption_key: bytes):
        ciphertext, tag, nonce = self._encrypt_message(encryption_key=encryption_key, message=message)

        payload = {
            "channel": channel_id,
            "content": {
                "ciphertext": ciphertext,
                "tag": tag,
                "nonce": nonce,
            },
            "message_id": str(uuid4()),
        }
        self._send_fcm(payload)

    @retry(
        wait=wait_exponential(multiplier=1, min=1, max=5),
        reraise=True,
        retry=retry_if_exception_type((httpx.NetworkError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        before_sleep=before_sleep_log(logger, logging.INFO),
    )
    def _send_fcm(self, payload: dict) -> None:
        url = f"{self._base_url}/messaging/send_fcm/"
        response = self.client.post(url, json=payload)
        if response.status_code == 400 and self._is_message_already_exists(response):
            logger.info("Message %s already delivered to Connect; treating as success", payload.get("message_id"))
            return
        _raise_for_status(response)

    def create_message(
        self,
        channel_id: str,
        encryption_key: bytes,
        text: str,
        attachments: Sequence[OutgoingAttachment] = (),
    ) -> None:
        """Send a message, with or without attachments, through PersonalID's create_message.

        A message with attachments also carries its text followed by LEGACY_APP_MESSAGE for apps
        that cannot show them, and expires after ATTACHMENT_EXPIRY.
        """
        message: dict = {"channel": channel_id, "message_id": str(uuid4())}
        if text:
            message["content"] = self._encrypted_content(encryption_key, text)

        # Encrypted once, outside the retried request, so a retry sends the same bytes
        attachment_parts = []
        if attachments:
            legacy_text = f"{text}\n\n{LEGACY_APP_MESSAGE}" if text else LEGACY_APP_MESSAGE
            message["content_legacy_msg"] = self._encrypted_content(encryption_key, legacy_text)
            message["expires_at"] = (timezone.now() + ATTACHMENT_EXPIRY).isoformat()
            message["attachments"] = []
            for index, attachment in enumerate(attachments):
                encrypted = encrypt_attachment(encryption_key, attachment.content)
                message["attachments"].append(
                    {"name": attachment.name, "type": attachment.content_type, "size": len(encrypted)}
                )
                # PersonalID takes the name from the JSON. The part only needs a filename to be
                # parsed as a file, so it gets the ASCII part name
                part_name = f"attachment_{index}"
                attachment_parts.append((part_name, (part_name, encrypted, "application/octet-stream")))

        timeout = _ATTACHMENT_UPLOAD_TIMEOUT if attachments else self.client.timeout
        self._post_create_message(message, attachment_parts, timeout)

    @retry(
        wait=wait_exponential(multiplier=1, min=1, max=5),
        reraise=True,
        retry=retry_if_exception_type((httpx.NetworkError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        before_sleep=before_sleep_log(logger, logging.INFO),
    )
    def _post_create_message(self, message: dict, attachment_parts: list, timeout: httpx.Timeout) -> None:
        url = f"{self._base_url}/messaging/create_message/"
        # No filename, so PersonalID's multipart parser reads it as a form field rather than a file
        message_part = ("message", (None, json.dumps(message).encode(), "application/json"))
        response = self.client.post(url, files=[message_part, *attachment_parts], timeout=timeout)
        if response.status_code == 400 and self._is_message_already_exists(response):
            logger.info("Message %s already delivered to Connect; treating as success", message["message_id"])
            return
        _raise_for_status(response)

    @staticmethod
    def _is_message_already_exists(response: httpx.Response) -> bool:
        try:
            return response.json().get("errors") == MESSAGE_ID_ALREADY_EXISTS
        except (ValueError, AttributeError):
            # ValueError covers both a non-JSON body and an undecodable one (UnicodeDecodeError);
            # AttributeError covers valid JSON that isn't an object. All mean "not the dedupe error".
            return False

    def _encrypted_content(self, encryption_key: bytes, text: str) -> dict[str, str]:
        ciphertext, tag, nonce = self._encrypt_message(encryption_key=encryption_key, message=text)
        return {"ciphertext": ciphertext, "tag": tag, "nonce": nonce}

    def _encrypt_message(self, encryption_key: bytes, message: str) -> tuple[str, str, str]:
        cipher = AES.new(encryption_key, AES.MODE_GCM)
        ciphertext_bytes, tag_bytes = cipher.encrypt_and_digest(message.encode())
        ciphertext = base64.b64encode(ciphertext_bytes).decode()
        tag = base64.b64encode(tag_bytes).decode()
        nonce = base64.b64encode(cipher.nonce).decode()
        return ciphertext, tag, nonce

    def _decrypt_message(self, encryption_key: bytes, ciphertext: str, tag: str, nonce: str) -> str:
        ciphertext_bytes = base64.b64decode(ciphertext)
        tag_bytes = base64.b64decode(tag)
        nonce_bytes = base64.b64decode(nonce)
        cipher = AES.new(encryption_key, AES.MODE_GCM, nonce=nonce_bytes)
        return cipher.decrypt_and_verify(ciphertext_bytes, tag_bytes).decode()

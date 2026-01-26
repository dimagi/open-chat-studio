import base64
import logging
from typing import TypedDict
from uuid import UUID, uuid4

import httpx
from Crypto.Cipher import AES
from django.conf import settings
from tenacity import before_sleep_log, retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger("ocs.channels.connect")


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
            timeout=10,
        )

    @retry(
        wait=wait_exponential(multiplier=1, min=1, max=5),
        reraise=True,
        retry=retry_if_exception_type(httpx.ConnectError),
        stop=stop_after_attempt(3),
        before_sleep=before_sleep_log(logger, logging.INFO),
    )
    def create_channel(self, connect_id: str, channel_source: str) -> dict:
        url = f"{self._base_url}/messaging/create_channel/"
        response = self.client.post(url, json={"connectid": str(connect_id), "channel_source": channel_source})
        response.raise_for_status()
        return response.json()

    def decrypt_messages(self, key: bytes, messages: list[Message]) -> list[str]:
        """
        Decrypts the `MessagePayload` list using the provided key and verifies the message authenticity

        Raises:
            ValueError if the message authenticity cannot be trusted
        """
        decrypted_messages = []
        for message in messages:
            message_text = self._decrypt_message(
                key, ciphertext=message["ciphertext"], tag=message["tag"], nonce=message["nonce"]
            )
            decrypted_messages.append(message_text)

        return decrypted_messages

    @retry(
        wait=wait_exponential(multiplier=1, min=1, max=5),
        reraise=True,
        retry=retry_if_exception_type(httpx.ConnectError),
        stop=stop_after_attempt(3),
        before_sleep=before_sleep_log(logger, logging.INFO),
    )
    def send_message_to_user(self, channel_id: str, message: str, encryption_key: bytes):
        ciphertext, tag, nonce = self._encrypt_message(key=encryption_key, message=message)

        payload = {
            "channel": channel_id,
            "content": {
                "ciphertext": ciphertext,
                "tag": tag,
                "nonce": nonce,
            },
            "message_id": str(uuid4()),
        }

        url = f"{self._base_url}/messaging/send_fcm/"
        response = self.client.post(url, json=payload)
        response.raise_for_status()

    def _encrypt_message(self, key: bytes, message: str) -> tuple[str, str, str]:
        cipher = AES.new(key, AES.MODE_GCM)
        ciphertext_bytes, tag_bytes = cipher.encrypt_and_digest(message.encode())
        ciphertext = base64.b64encode(ciphertext_bytes).decode()
        tag = base64.b64encode(tag_bytes).decode()
        nonce = base64.b64encode(cipher.nonce).decode()
        return ciphertext, tag, nonce

    def _decrypt_message(self, key: bytes, ciphertext: str, tag: str, nonce: str) -> str:
        ciphertext = base64.b64decode(ciphertext)
        tag = base64.b64decode(tag)
        nonce = base64.b64decode(nonce)
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(ciphertext, tag).decode()

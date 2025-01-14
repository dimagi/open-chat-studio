import base64
import logging
from typing import TypedDict
from uuid import UUID, uuid4

import requests
from Crypto.Cipher import AES
from django.conf import settings
from requests.auth import HTTPBasicAuth
from tenacity import before_sleep_log, retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger("connectid-api")


class Message(TypedDict):
    timestamp: str
    message_id: UUID
    ciphertext: str
    tag: str
    nonce: str


class NewMessagePayload(TypedDict):
    channel_id: UUID
    messages: list[Message]


class ConnectClient:
    def __init__(self):
        self._base_url = settings.CONNECT_ID_SERVER_BASE_URL
        self._auth = HTTPBasicAuth(settings.CONNECT_MESSAGING_SERVER_ID, settings.CONNECT_MESSAGING_SERVER_SECRET)

    @retry(
        wait=wait_exponential(multiplier=1, min=1, max=5),
        reraise=True,
        retry=retry_if_exception_type(requests.ConnectionError),
        stop=stop_after_attempt(3),
        before_sleep=before_sleep_log(logger, logging.INFO),
    )
    def create_channel(self, connect_id: UUID, channel_source: str) -> UUID:
        url = f"{self._base_url}/messaging/create_channel"
        response = requests.post(
            url, json={"connectid": str(connect_id), "channel_source": channel_source}, auth=self._auth, timeout=10
        )
        response.raise_for_status()
        return UUID(response.json()["channel_id"])

    def decrypt_messages(self, key: bytes, messages: list[Message]) -> list[str]:
        """
        Decrypts the `MessagePayload` list using the provided key and verifies the message authenticity

        Raises:
            ValueError if the message authenticity cannot be trusted
        """
        decrypted_messages = []
        for message in messages:
            nonce = base64.b64decode(message["nonce"])
            tag = base64.b64decode(message["tag"])
            ciphertext = base64.b64decode(message["ciphertext"])
            cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
            decrypted_messages.append(cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8"))

        return decrypted_messages

    @retry(
        wait=wait_exponential(multiplier=1, min=1, max=5),
        reraise=True,
        retry=retry_if_exception_type(requests.ConnectionError),
        stop=stop_after_attempt(3),
        before_sleep=before_sleep_log(logger, logging.INFO),
    )
    def send_message_to_user(self, channel_id: str, message: str, encryption_key: bytes):
        ciphertext_bytes, tag_bytes, nonce_bytes = self._encrypt_message(key=encryption_key, message=message)

        payload = {
            "channel": channel_id,
            "content": {
                "ciphertext": base64.b64encode(ciphertext_bytes).decode(),
                "tag": base64.b64encode(tag_bytes).decode(),
                "nonce": base64.b64encode(nonce_bytes).decode(),
            },
            "message_id": uuid4().hex,
        }

        url = f"{self._base_url}/messaging/send_fcm/"
        response = requests.post(url, json=payload, auth=self._auth, timeout=10)
        if response.status_code == 403:
            logger.info("User did not give consent to receive messages")
        else:
            response.raise_for_status()

    def _encrypt_message(self, key: bytes, message: str) -> tuple[bytes, bytes, bytes]:
        cipher = AES.new(key, AES.MODE_GCM)
        ciphertext_bytes, tag_bytes = cipher.encrypt_and_digest(message.encode("utf-8"))
        nonce = cipher.nonce
        return ciphertext_bytes, tag_bytes, nonce

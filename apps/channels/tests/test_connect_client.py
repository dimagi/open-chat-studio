import base64
import json
import os
from uuid import uuid4

import responses
from Crypto.Cipher import AES
from django.conf import settings

from apps.channels.clients.connect_client import ConnectClient, MessagePayload


class TestConnectClient:
    def test_decrypt_messages(self):
        encryption_key = os.urandom(32)
        cipher = AES.new(encryption_key, mode=AES.MODE_GCM)
        ciphertext, tag = cipher.encrypt_and_digest(b"this is a secret message")

        connect_client = ConnectClient()
        payload = MessagePayload(
            timestamp="2021-10-10T10:10:10Z",
            message_id=uuid4(),
            ciphertext=base64.b64encode(
                ciphertext,
            ).decode(),
            tag=base64.b64encode(tag).decode(),
            nonce=base64.b64encode(cipher.nonce).decode(),
        )
        messages = connect_client.decrypt_messages(key=encryption_key, messages=[payload])
        assert messages[0] == "this is a secret message"

    def test_encrypt_message(self):
        encryption_key = os.urandom(32)
        connect_client = ConnectClient()
        ciphertext, tag, nonce = connect_client._encrypt_message(key=encryption_key, message="this is a secret message")
        assert isinstance(ciphertext, bytes)
        assert isinstance(tag, bytes)
        assert isinstance(nonce, bytes)

        cipher = AES.new(encryption_key, AES.MODE_GCM, nonce=nonce)
        assert cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8") == "this is a secret message"

    @responses.activate
    def test_send_message_to_user_1(self):
        response = responses.Response(
            method="POST",
            url=f"{settings.CONNECT_ID_SERVER_BASE_URL}/messaging/send_fcm/",
            json={"message_id": "765aec754eacf3221"},
            status=200,
        )
        responses.add(response)

        channel_id = uuid4().hex
        raw_message = "Hi there human"
        encryption_key = os.urandom(32)

        connect_client = ConnectClient()
        connect_client.send_message_to_user(channel_id, raw_message=raw_message, encryption_key=encryption_key)
        request = responses.calls[0].request
        assert request.headers["Authorization"].split(" ")[0] == "Basic"
        request_data = json.loads(request.body)
        message_content = request_data["content"]

        assert "content" in request_data
        assert "channel" in request_data
        assert "message_id" in request_data

        assert "nonce" in message_content
        assert "tag" in message_content
        assert "ciphertext" in message_content

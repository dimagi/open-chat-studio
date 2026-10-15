"""Fixtures shared by tests in more than one app. Registered in `apps/conftest.py`."""

import base64
import hashlib
import hmac
import json
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings

from apps.service_providers.messaging_service import SlackService
from apps.teams.export import seal as seal_mod
from apps.utils.tests.team_helpers import make_team_with_resources


@pytest.fixture()
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return seal_mod.load_public_key(public_pem), private


@pytest.fixture()
def slack_service():
    service = SlackService(slack_team_id="123", slack_installation_id=1)
    mock_client = Mock(
        conversations_list=Mock(
            return_value=[
                {"channels": [{"id": "123", "name": "channel1"}]},
                {"channels": [{"id": "345", "name": "channel2"}]},
            ]
        )
    )
    service.client = mock_client
    return service


@pytest.fixture()
def connect_request_headers():
    """Build the HMAC header that CommCare Connect signs its requests with."""

    def _headers(payload: dict) -> dict:
        msg = json.dumps(payload).encode("utf-8")
        key = settings.COMMCARE_CONNECT_SERVER_SECRET.encode()
        digest = hmac.new(key=key, msg=msg, digestmod=hashlib.sha256).digest()
        return {"X-MAC-DIGEST": base64.b64encode(digest)}

    return _headers


@pytest.fixture()
def team_with_resources(db):
    return make_team_with_resources()

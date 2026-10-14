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
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.experiment import SourceMaterialFactory
from apps.utils.factories.service_provider_factories import (
    LlmProviderFactory,
    LlmProviderModelFactory,
    VoiceProviderFactory,
)
from apps.utils.factories.team import TeamWithUsersFactory


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


def make_team_with_resources():
    """A team with one of each resource that a pipeline node's options list draws from."""
    team = TeamWithUsersFactory.create()
    LlmProviderFactory.create(team=team, type="openai", name="Prod OpenAI")
    LlmProviderModelFactory.create(team=team, type="openai")
    VoiceProviderFactory.create(team=team, name="Prod Polly")
    SourceMaterialFactory.create(team=team, topic="Returns policy")
    # Pinned to None so CollectionFactory's SubFactories don't add an LlmProvider to this team.
    CollectionFactory.create(
        team=team, name="Policy docs", is_index=False, llm_provider=None, embedding_provider_model=None
    )
    return team

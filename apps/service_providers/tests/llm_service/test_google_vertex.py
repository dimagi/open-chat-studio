import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from apps.service_providers.llm_service.main import GoogleVertexAILlmService


@pytest.fixture(scope="module")
def service_account():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    return {
        "type": "service_account",
        "project_id": "ocs-test",
        "private_key_id": "1",
        "private_key": pem,
        "client_email": "bot@ocs-test.iam.gserviceaccount.com",
        "client_id": "1",
        "token_uri": "https://oauth2.googleapis.com/token",
    }


def test_vertex_uses_the_genai_client_on_its_vertex_backend(service_account):
    model = GoogleVertexAILlmService(credentials_json=service_account, location="europe-west4")._chat_model(
        "gemini-2.5-flash"
    )

    assert type(model).__name__ == "ChatGoogleGenerativeAI"
    assert (model.vertexai, model.project, model.location) == (True, "ocs-test", "europe-west4")


def test_vertex_credentials_are_scoped_and_can_sign(service_account):
    """google-genai uses passed-in credentials as they are, and an unscoped service account cannot get a token."""
    credentials = GoogleVertexAILlmService(credentials_json=service_account).credentials

    assert credentials.scopes == ["https://www.googleapis.com/auth/cloud-platform"]
    assert credentials._make_authorization_grant_assertion()


def test_a_provider_saved_with_api_transport_still_loads(service_account):
    """The field is gone, but configs saved before it was dropped still carry it."""
    service = GoogleVertexAILlmService(credentials_json=service_account, api_transport="grpc")

    assert service._chat_model("gemini-2.5-flash").vertexai is True

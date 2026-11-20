from apps.service_providers.llm_service.main import GoogleLlmService


def test_google_uses_v1beta_so_preview_models_resolve():
    """google-genai 3.x defaults the Gemini Developer API to v1, which does not serve preview models."""
    model = GoogleLlmService(google_api_key="test-key")._chat_model("gemini-3-flash-preview")

    assert model.client._api_client._http_options.api_version == "v1beta"

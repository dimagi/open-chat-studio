import pydantic
from openai import OpenAI


def build_openai_client(
    *, api_key: pydantic.SecretStr, organization: str | None, base_url: str | None, **client_kwargs
) -> OpenAI:
    """An OpenAI client for a provider's config. ``client_kwargs`` go to the client (e.g. timeout, max_retries)."""
    return OpenAI(api_key=api_key.get_secret_value(), organization=organization, base_url=base_url, **client_kwargs)

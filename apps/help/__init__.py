from typing import Literal

from pydantic import BaseModel


class SystemAgentModel(BaseModel):
    provider: Literal["openai", "anthropic"]
    model: str
    key: str

    def init_model(self):
        from langchain.chat_models import init_chat_model

        return init_chat_model(self.model, model_provider=self.provider, **self.model_kwargs)

    @property
    def model_kwargs(self) -> dict:
        config_key = {
            "openai": "openai_api_key",
            "anthropic": "anthropic_api_key",
        }.get(self.provider)

        if not config_key:
            raise Exception(f"Unknown provider: {self.provider}")
        return {config_key: self.key}


def get_system_agent_models(models, api_keys):
    result = []
    for model in models:
        provider, name = model.split(":", 1)
        key = api_keys.get(provider, None)
        if not key:
            raise Exception(f"System agent API Key not found: {provider}. Update `SYSTEM_AGENT_API_KEYS`.")

        result.append(SystemAgentModel(provider=provider, model=name, key=key))
    return result

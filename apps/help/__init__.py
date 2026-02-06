from typing import Literal

from pydantic import BaseModel


class SystemAgentModel(BaseModel):
    provider: Literal["openai", "anthropic"]
    model: str
    key: str
    url: str | None = None

    def init_model(self):
        from langchain.chat_models import init_chat_model

        return init_chat_model(self.model, model_provider=self.provider, **self.model_kwargs)

    @property
    def model_kwargs(self):
        match self.provider:
            case "openai":
                return {"openai_api_key": self.key, "openai_api_base": self.url}
            case "anthropic":
                return {"anthropic_api_key": self.key, "anthropic_api_url": self.url}
        raise Exception(f"Unknown provider: {self.provider}")

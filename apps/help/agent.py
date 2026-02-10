from typing import Literal

from django.conf import settings


def build_system_agent(
    mode: Literal["high", "low"],
    system_prompt: str,
    tools: list | None = None,
    middleware: list | None = None,
    **kwargs,
):
    from langchain.agents import create_agent
    from langchain.agents.middleware import ModelFallbackMiddleware, ModelRetryMiddleware

    from apps.service_providers.llm_service.retry import get_retry_middleware

    model_configs = settings.SYSTEM_AGENT_MODELS_HIGH if mode == "high" else settings.SYSTEM_AGENT_MODELS_LOW
    if not model_configs:
        raise Exception("no system agent models configured")

    first_model, *fallback_models = model_configs
    middleware = middleware or []
    if not any(isinstance(ware, ModelRetryMiddleware) for ware in middleware):
        middleware.append(get_retry_middleware())
    if fallback_models:
        middleware.append(ModelFallbackMiddleware(*[config.init_model() for config in fallback_models]))
    return create_agent(first_model.init_model(), tools, system_prompt=system_prompt, middleware=middleware, **kwargs)

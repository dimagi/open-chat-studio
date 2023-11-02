from django.apps import AppConfig


class LlmProvidersConfig(AppConfig):
    name = "apps.llm_providers"
    label = "llm_providers"

    # TODO: remove this app once migrations have been run

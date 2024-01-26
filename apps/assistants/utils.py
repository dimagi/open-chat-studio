from apps.service_providers.models import LlmProviderType


def get_llm_providers_for_assistants(team):
    return team.llmprovider_set.filter(type=LlmProviderType.openai)

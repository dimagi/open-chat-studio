from apps.service_providers.models import LlmProviderTypes


def get_llm_providers_for_assistants(team):
    return team.llmprovider_set.filter(type=LlmProviderTypes.openai)

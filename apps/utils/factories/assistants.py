import factory
import factory.django

from apps.utils.factories.service_provider_factories import LlmProviderModelFactory


class OpenAiAssistantFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "assistants.OpenAiAssistant"

    team = factory.SubFactory("apps.utils.factories.team.TeamFactory")
    llm_provider = factory.SubFactory(
        "apps.utils.factories.service_provider_factories.LlmProviderFactory", team=factory.SelfAttribute("..team")
    )
    llm_provider_model = factory.SubFactory(LlmProviderModelFactory, team=factory.SelfAttribute("..team"))

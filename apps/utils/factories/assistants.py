import factory


class OpenAiAssistantFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "assistants.OpenAiAssistant"

    team = factory.SubFactory("apps.utils.factories.team.TeamFactory")
    llm_provider = factory.SubFactory(
        "apps.utils.factories.service_provider_factories.LlmProviderFactory", team=factory.SelfAttribute("..team")
    )

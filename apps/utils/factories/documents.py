import factory


class CollectionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "documents.Collection"

    team = factory.SubFactory("apps.utils.factories.team.TeamFactory")
    llm_provider = factory.SubFactory(
        "apps.utils.factories.service_provider_factories.LlmProviderFactory", team=factory.SelfAttribute("..team")
    )
    embedding_provider_model = factory.SubFactory(
        "apps.utils.factories.service_provider_factories.EmbeddingProviderModelFactory",
        team=factory.SelfAttribute("..team"),
    )

import factory

from apps.experiments.models import ConsentForm, Experiment, Prompt, SourceMaterial
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory


class PromptFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Prompt

    owner = factory.SubFactory(UserFactory)
    name = "Some name"
    description = "This is a description"
    team = factory.SubFactory(TeamFactory)


class ConsentFormFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ConsentForm

    name = "Consent form"
    consent_text = "Do you give consent?"
    is_default = True
    team = factory.SubFactory(TeamFactory)


class SourceMaterialFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SourceMaterial

    owner = factory.SubFactory(UserFactory)
    topic = "Some source"
    description = "Some description"
    material = "material"
    team = factory.SubFactory(TeamFactory)


class ExperimentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Experiment

    owner = factory.SubFactory(UserFactory)
    name = factory.Faker("name")
    chatbot_prompt = factory.SubFactory(PromptFactory)
    consent_form = factory.SubFactory(ConsentFormFactory)
    team = factory.LazyAttribute(lambda obj: obj.chatbot_prompt.team)
    llm_provider = factory.SubFactory(LlmProviderFactory)

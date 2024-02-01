import factory

from apps.experiments import models
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory


class SurveyFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.Survey

    name = "Name"
    url = "https://example.com/participant={participant_id}"
    team = factory.SubFactory(TeamFactory)


class PromptFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.Prompt

    owner = factory.SubFactory(UserFactory)
    name = "Some name"
    description = "This is a description"
    team = factory.SubFactory(TeamFactory)
    prompt = factory.Faker("text")


class ConsentFormFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.ConsentForm

    name = "Consent form"
    consent_text = "Do you give consent?"
    is_default = True
    team = factory.SubFactory(TeamFactory)


class SourceMaterialFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.SourceMaterial

    owner = factory.SubFactory(UserFactory)
    topic = "Some source"
    description = "Some description"
    material = "material"
    team = factory.SubFactory(TeamFactory)


class ExperimentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.Experiment

    team = factory.SubFactory(TeamFactory)
    owner = factory.SubFactory(UserFactory)
    name = factory.Faker("name")
    llm = factory.Faker("random_element", elements=["gpt-3.5-turbo", "gpt-4"])
    chatbot_prompt = factory.SubFactory(PromptFactory, team=factory.SelfAttribute("..team"))
    consent_form = factory.SubFactory(ConsentFormFactory, team=factory.SelfAttribute("..team"))
    llm_provider = factory.SubFactory(LlmProviderFactory, team=factory.SelfAttribute("..team"))
    pre_survey = factory.SubFactory(SurveyFactory, team=factory.SelfAttribute("..team"))
    public_id = factory.Faker("uuid4")


class ExperimentSessionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.ExperimentSession

    experiment = factory.SubFactory(ExperimentFactory)
    team = factory.LazyAttribute(lambda obj: obj.experiment.team)

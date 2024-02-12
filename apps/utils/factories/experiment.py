import factory

from apps.experiments import models
from apps.utils.factories.service_provider_factories import LlmProviderFactory, VoiceProviderFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory


class SurveyFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.Survey

    name = "Name"
    url = "https://example.com/participant={participant_id}"
    team = factory.SubFactory(TeamFactory)


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


class SyntheticVoiceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.SyntheticVoice

    name = factory.Faker("name")
    neural = True
    language = "English"
    language_code = "en"
    gender = "male"
    service = "AWS"


class ExperimentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.Experiment

    team = factory.SubFactory(TeamFactory)
    owner = factory.SubFactory(UserFactory)
    name = factory.Faker("name")
    llm = factory.Faker("random_element", elements=["gpt-3.5-turbo", "gpt-4"])
    prompt_text = "You are a helpful assistant"
    consent_form = factory.SubFactory(ConsentFormFactory, team=factory.SelfAttribute("..team"))
    llm_provider = factory.SubFactory(LlmProviderFactory, team=factory.SelfAttribute("..team"))
    pre_survey = factory.SubFactory(SurveyFactory, team=factory.SelfAttribute("..team"))
    public_id = factory.Faker("uuid4")
    synthetic_voice = factory.SubFactory(SyntheticVoiceFactory)
    voice_provider = factory.SubFactory(VoiceProviderFactory)


class ExperimentSessionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.ExperimentSession

    experiment = factory.SubFactory(ExperimentFactory)
    team = factory.LazyAttribute(lambda obj: obj.experiment.team)

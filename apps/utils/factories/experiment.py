import factory

from apps.chat.models import Chat, ChatMessage
from apps.experiments import models
from apps.utils.factories.service_provider_factories import (
    LlmProviderFactory,
    LlmProviderModelFactory,
    VoiceProviderFactory,
)
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
    is_default = False
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

    name = factory.Sequence(lambda n: f"Test Voice Provider {n}")
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
    name = factory.Sequence(lambda n: f"Test Experiment {n}")
    prompt_text = "You are a helpful assistant"
    consent_form = factory.SubFactory(ConsentFormFactory, team=factory.SelfAttribute("..team"))
    llm_provider = factory.SubFactory(LlmProviderFactory, team=factory.SelfAttribute("..team"))
    llm_provider_model = factory.SubFactory(LlmProviderModelFactory, team=factory.SelfAttribute("..team"))
    pre_survey = factory.SubFactory(SurveyFactory, team=factory.SelfAttribute("..team"))
    public_id = factory.Faker("uuid4")
    synthetic_voice = factory.SubFactory(SyntheticVoiceFactory)
    voice_provider = factory.SubFactory(VoiceProviderFactory)


class VersionedExperimentFactory(ExperimentFactory):
    working_version = factory.SubFactory(ExperimentFactory, version_number=2, team=factory.SelfAttribute("..team"))
    version_number = 1


class ChatMessageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ChatMessage


class ChatFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Chat

    team = factory.SubFactory(TeamFactory)


class ParticipantFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.Participant

    team = factory.SubFactory(TeamFactory)
    identifier = factory.Faker("uuid4")
    name = factory.Sequence(lambda n: f"Test Participant {n}")


class ExperimentSessionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.ExperimentSession

    experiment = factory.SubFactory(ExperimentFactory)
    team = factory.LazyAttribute(lambda obj: obj.experiment.team)
    chat = factory.SubFactory(ChatFactory, team=factory.SelfAttribute("..team"))
    participant = factory.SubFactory(ParticipantFactory, team=factory.SelfAttribute("..team"))
    experiment_channel = factory.SubFactory(
        "apps.utils.factories.channels.ExperimentChannelFactory", team=factory.SelfAttribute("..team")
    )

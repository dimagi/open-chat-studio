from pydantic import BaseModel, Field

from .models import Experiment, SourceMaterial


class SafetyLayerData(BaseModel):
    name: str
    prompt_text: str
    messages_to_review: str
    default_response_to_user: str | None = ""
    prompt_to_bot: str | None = ""


class SurveyData(BaseModel):
    name: str
    url: str
    confirmation_text: str


class ConsentFormData(BaseModel):
    name: str
    consent_text: str
    capture_identifier: bool
    identifier_label: str
    identifier_type: str
    is_default: bool
    confirmation_text: str


class SyntheticVoiceData(BaseModel):
    name: str
    neural: bool
    language: str
    language_code: str
    gender: str | None
    service: str
    voice_provider: str | None
    file: str | None


class ExperimentVersionData(BaseModel):
    name: str
    description: str | None = ""
    assistant_id: str | None
    temperature: float
    prompt_text: str | None = ""
    input_formatter: str | None = ""
    safety_layers: list[SafetyLayerData] = Field(default_factory=list)
    is_active: bool
    source_material: SourceMaterial | None
    seed_message: str | None = ""
    pre_survey: SurveyData | None
    post_survey: SurveyData | None
    public_id: str
    consent_form: ConsentFormData
    synthetic_voice: SyntheticVoiceData | None
    conversational_consent_enabled: bool
    safety_violation_notification_emails: list[str] = []
    max_token_limit: int
    voice_response_behaviour: str
    file_ids: list[int] = []
    tool_list: list[str] = []

    class Config:
        arbitrary_types_allowed = True


def populate_experiment_version_data(experiment: Experiment) -> ExperimentVersionData | None:
    try:
        safety_layers = [
            SafetyLayerData(
                name=layer.name,
                prompt_text=layer.prompt_text,
                messages_to_review=layer.messages_to_review,
                default_response_to_user=layer.default_response_to_user,
                prompt_to_bot=layer.prompt_to_bot,
            )
            for layer in experiment.safety_layers.all()
        ]
        pre_survey = (
            SurveyData(
                name=experiment.pre_survey.name,
                url=experiment.pre_survey.url,
                confirmation_text=experiment.pre_survey.confirmation_text,
            )
            if experiment.pre_survey
            else None
        )

        post_survey = (
            SurveyData(
                name=experiment.post_survey.name,
                url=experiment.post_survey.url,
                confirmation_text=experiment.post_survey.confirmation_text,
            )
            if experiment.post_survey
            else None
        )

        consent_form = ConsentFormData(
            name=experiment.consent_form.name,
            consent_text=experiment.consent_form.consent_text,
            capture_identifier=experiment.consent_form.capture_identifier,
            identifier_label=experiment.consent_form.identifier_label,
            identifier_type=experiment.consent_form.identifier_type,
            is_default=experiment.consent_form.is_default,
            confirmation_text=experiment.consent_form.confirmation_text,
        )

        synthetic_voice = (
            SyntheticVoiceData(
                name=experiment.synthetic_voice.name,
                neural=experiment.synthetic_voice.neural,
                language=experiment.synthetic_voice.language,
                language_code=experiment.synthetic_voice.language_code,
                gender=experiment.synthetic_voice.gender,
                service=experiment.synthetic_voice.service,
                voice_provider=experiment.synthetic_voice.voice_provider,
                file=experiment.synthetic_voice.file,
            )
            if experiment.synthetic_voice
            else None
        )

        version_data = ExperimentVersionData(
            name=experiment.name,
            description=experiment.description,
            assistant_id=experiment.assistant.id if experiment.assistant else None,
            temperature=experiment.temperature,
            prompt_text=experiment.prompt_text,
            input_formatter=experiment.input_formatter,
            safety_layers=safety_layers,
            is_active=experiment.is_active,
            source_material=experiment.source_material,
            seed_message=experiment.seed_message,
            pre_survey=pre_survey,
            post_survey=post_survey,
            public_id=experiment.public_id,
            consent_form=consent_form,
            synthetic_voice=synthetic_voice,
            conversational_consent_enabled=experiment.conversational_consent_enabled,
            safety_violation_notification_emails=experiment.safety_violation_notification_emails,
            max_token_limit=experiment.max_token_limit,
            voice_response_behaviour=experiment.voice_response_behaviour,
            file_ids=[file.id for file in experiment.files.all()],
            tool_list=experiment.tools,
        )
        return version_data
    except Exception as e:
        print(f"Error populating experiment version data: {e}")
        return None

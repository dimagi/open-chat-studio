from uuid import UUID

from experiments.models import Experiment, SourceMaterial
from pydantic import BaseModel, Field, HttpUrl


class SafetyLayerData(BaseModel):
    name: str
    prompt_text: str
    messages_to_review: str
    default_response_to_user: str | None = ""
    prompt_to_bot: str | None = ""


class SurveyData(BaseModel):
    name: str
    url: HttpUrl
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
    public_id: UUID
    consent_form: ConsentFormData
    synthetic_voice: SyntheticVoiceData | None
    conversational_consent_enabled: bool
    safety_violation_notification_emails: list[str] = []
    max_token_limit: int
    voice_response_behaviour: str
    file_ids: list[int] = []
    tool_list: list[str] = []


def populate_experiment_version_data(experiment: Experiment) -> ExperimentVersionData | None:
    try:
        return ExperimentVersionData(
            name=experiment.name,
            description=experiment.description,
            llm_provider_id=experiment.llm_provider.id if experiment.llm_provider else None,
            llm=experiment.llm,
            assistant_id=experiment.assistant.id if experiment.assistant else None,
            temperature=experiment.temperature,
            prompt_text=experiment.prompt_text,
            input_formatter=experiment.input_formatter,
            safety_layer_ids=[layer.id for layer in experiment.safety_layers.all()],
            is_active=experiment.is_active,
            source_material_id=experiment.source_material.id if experiment.source_material else None,
            seed_message=experiment.seed_message,
            pre_survey_id=experiment.pre_survey.id if experiment.pre_survey else None,
            post_survey_id=experiment.post_survey.id if experiment.post_survey else None,
            public_id=experiment.public_id,
            consent_form_id=experiment.consent_form.id,
            voice_provider_id=experiment.voice_provider.id if experiment.voice_provider else None,
            synthetic_voice_id=experiment.synthetic_voice.id if experiment.synthetic_voice else None,
            conversational_consent_enabled=experiment.conversational_consent_enabled,
            safety_violation_notification_emails=experiment.safety_violation_notification_emails,
            max_token_limit=experiment.max_token_limit,
            voice_response_behaviour=experiment.voice_response_behaviour,
            file_ids=[file.id for file in experiment.files.all()],
            tool_list=experiment.tools,
        )
    except Exception as e:
        print(f"Error populating experiment version data: {e}")
        return None

from uuid import UUID

from experiments.models import Experiment
from pydantic import BaseModel


class ExperimentVersionData(BaseModel):
    name: str
    description: str | None = ""
    llm_provider_id: int | None
    llm: str
    assistant_id: int | None
    temperature: float
    prompt_text: str | None = ""
    input_formatter: str | None = ""
    safety_layer_ids: list[int] = []
    is_active: bool
    source_material_id: int | None
    seed_message: str | None = ""
    pre_survey_id: int | None
    post_survey_id: int | None
    public_id: UUID
    consent_form_id: int
    voice_provider_id: int | None
    synthetic_voice_id: int | None
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

EXPERIMENT_FIELDS = [
    "owner",
    "name",
    "llm_provider",
    "llm",
    "temperature",
    "prompt_text",
    "input_formatter",
    "safety_layers",
    "is_active",
    "tools_enabled",
    "source_material",
    "seed_message",
    "pre_survey",
    "post_survey",
    "consent_form",
    "voice_provider",
    "synthetic_voice",
    "no_activity_config",
    "conversational_consent_enabled",
    "team",
]

SOURCE_MATERIAL_FIELDS = ["owner", "topic", "description", "material", "team"]
SAFETY_LAYER_FIELDS = ["prompt_text", "messages_to_review", "default_response_to_user", "prompt_to_bot", "team"]
CONSENT_FORM_FIELDS = [
    "name",
    "consent_text",
    "capture_identifier",
    "identifier_label",
    "identifier_type",
    "confirmation_text",
    "team",
]

EXPERIMENT_CHANNEL_FIELDS = [
    "name",
    "experiment",
    "active",
    "extra_data",
    "platform",
    "messaging_provider",
]

NO_ACTIVITY_CONFIG_FIELDS = ["message_for_bot", "name", "max_pings", "ping_after", "team"]

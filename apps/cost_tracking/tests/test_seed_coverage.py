"""CI gate: every global non-deprecated LlmProviderModel must have at least
one active PricingRule covering llm_input and llm_output. Failing this means
a model was added to default_models.py without corresponding pricing in
the seed JSON.
"""

import pytest
from django.core.management import call_command

from apps.cost_tracking.models import PricingRule, ServiceKind
from apps.service_providers.models import LlmProviderModel

REQUIRED_KINDS = (ServiceKind.LLM_INPUT, ServiceKind.LLM_OUTPUT)

# Explicit allow-list of (provider, model) pairs not required to have
# llm_input / llm_output pricing. Every entry needs a one-line reason so a
# reviewer can decide whether the gap should still hold. Adding a new line
# here is a deliberate gesture, not a routine fix.
KNOWN_UNPRICED: set[tuple[str, str]] = {
    # Transcription model — billed per audio minute, not per token.
    ("groq", "whisper-large-v3-turbo"),
    # Pricing not yet in LiteLLM; rate-change workflow will fill these in.
    ("groq", "gemma2-9b-it"),
    ("openai", "gpt-5.3"),
    ("openai", "gpt-5.3-instant"),
    ("perplexity", "llama-3.1-sonar-large-128k-chat"),
    ("perplexity", "llama-3.1-sonar-small-128k-chat"),
}


@pytest.mark.django_db()
def test_every_global_model_has_input_and_output_pricing():
    """Load the seed, then assert that every non-deprecated global model
    has rules for both required service kinds — except those explicitly
    allow-listed in KNOWN_UNPRICED.
    """
    call_command("load_ai_pricing", verbosity=0)

    models = LlmProviderModel.objects.filter(team__isnull=True, deprecated=False)
    missing: list[str] = []

    for model in models:
        if (model.type, model.name) in KNOWN_UNPRICED:
            continue
        for kind in REQUIRED_KINDS:
            has_rule = PricingRule.objects.filter(
                team__isnull=True,
                provider_type=model.type,
                model_name=model.name,
                service_kind=kind,
                effective_to__isnull=True,
            ).exists()
            if not has_rule:
                missing.append(f"  - {model.type}/{model.name} missing {kind}")

    assert not missing, (
        "The following global, non-deprecated LlmProviderModels are missing "
        "active PricingRules. Either add them to apps/cost_tracking/seed_data/llm_pricing.json "
        "or add the (provider, model) pair to KNOWN_UNPRICED with a reason:\n" + "\n".join(missing)
    )

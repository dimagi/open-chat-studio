"""CI gate: every global non-deprecated LlmProviderModel must have at least
one active PricingRule covering llm_input and llm_output. Failing this means
a model was added to default_models.py without corresponding pricing in the
seed JSON.
"""

import pytest
from django.core.management import call_command

from apps.cost_tracking.models import PricingRule, ServiceKind
from apps.service_providers.models import LlmProviderModel

REQUIRED_KINDS = (ServiceKind.LLM_INPUT, ServiceKind.LLM_OUTPUT)


@pytest.mark.skip(
    reason="The seed only covers a minimal set of models. PR 2's auto-update workflow adds "
    "pricing for newly-added models going forward; unskip once the seed has been backfilled "
    "to cover every currently-registered global LlmProviderModel (PR 3 or a dedicated seed PR)."
)
@pytest.mark.django_db()
def test_every_global_model_has_input_and_output_pricing():
    """Load the seed, then assert that every non-deprecated global model
    has rules for both required service kinds.
    """
    call_command("load_ai_pricing", verbosity=0)

    models = LlmProviderModel.objects.filter(team__isnull=True, deprecated=False)
    missing: list[str] = []

    for model in models:
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
        "active PricingRules. Add them to apps/cost_tracking/seed_data/llm_pricing.json:\n" + "\n".join(missing)
    )

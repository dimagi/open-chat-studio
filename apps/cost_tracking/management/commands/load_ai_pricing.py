"""Idempotent loader for the in-repo pricing seed.

Reads `apps/cost_tracking/seed_data/llm_pricing.json` and upserts global
PricingRule rows via supersession. Safe to run multiple times: a rule whose
active price matches the seed is a no-op; a rule with a different price gets
its old row closed (`effective_to=now()`) and a new active row inserted.

Called from the 0002_seed_pricing data migration on deploy, and runnable
locally as `manage.py load_ai_pricing` after editing the seed JSON.
"""

import json
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.cost_tracking.models import PricingRule, PricingSource
from apps.cost_tracking.services.pricing import PricingKey

SEED_PATH = Path(__file__).resolve().parents[2] / "seed_data" / "llm_pricing.json"


class Command(BaseCommand):
    help = "Load the in-repo LLM pricing seed into PricingRule (idempotent, supersedes on change)."

    def add_arguments(self, parser):
        """Expose --path so devs can point the loader at an alternate seed file."""
        parser.add_argument(
            "--path",
            default=str(SEED_PATH),
            help="Path to the seed JSON file (default: apps/cost_tracking/seed_data/llm_pricing.json)",
        )

    def handle(self, *args, path: str, **options):
        """Walk the seed JSON and upsert each rule, tallying outcomes for stdout."""
        seed = json.loads(Path(path).read_text())
        stats = {"unchanged": 0, "created": 0, "superseded": 0}
        for entry in seed:
            for rule in entry["rules"]:
                outcome = upsert_global_rule(
                    PricingKey(
                        provider_type=entry["provider_type"],
                        model_name=entry["model_name"],
                        service_kind=rule["service_kind"],
                    ),
                    unit_price=Decimal(rule["unit_price"]),
                    currency=rule.get("currency", "USD"),
                    source=PricingSource.SEED,
                )
                stats[outcome] += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"load_ai_pricing: {stats['unchanged']} unchanged, "
                f"{stats['created']} created, {stats['superseded']} superseded"
            )
        )


def upsert_global_rule(
    key: PricingKey,
    *,
    unit_price: Decimal,
    currency: str = "USD",
    source: str = PricingSource.SEED,
) -> str:
    """Apply the supersession pattern for a single (provider, model, service_kind).

    Returns one of "unchanged", "created", or "superseded".
    """
    active = PricingRule.objects.filter(
        team__isnull=True,
        provider_type=key.provider_type,
        model_name=key.model_name,
        service_kind=key.service_kind,
        effective_to__isnull=True,
    )

    # No-op fast path: same rate already active.
    if active.filter(unit_price=unit_price, currency=currency).exists():
        return "unchanged"

    now = timezone.now()
    with transaction.atomic():
        closed = active.update(effective_to=now)
        # The post_save signal on this create() invalidates the resolver cache
        # for this key, which covers both the new row and the just-closed row
        # (same PricingKey). No explicit invalidate needed.
        PricingRule.objects.create(
            team=None,
            provider_type=key.provider_type,
            model_name=key.model_name,
            service_kind=key.service_kind,
            unit_price=unit_price,
            currency=currency,
            source=source,
            effective_from=now,
        )

    return "superseded" if closed else "created"

"""Duplicate an evaluator together with its tag rules."""

from copy import deepcopy

from django.db import transaction

from apps.evaluations.models import Evaluator, EvaluatorTagRule

COPY_NAME_SUFFIX = " (copy)"


@transaction.atomic
def copy_evaluator(source: Evaluator) -> Evaluator:
    """A new active evaluator with the source's configuration and tag rules, and none of its history."""
    max_length = Evaluator._meta.get_field("name").max_length
    base_name = source.name[: max_length - len(COPY_NAME_SUFFIX)]
    duplicate = Evaluator.objects.create(
        team_id=source.team_id,
        name=f"{base_name}{COPY_NAME_SUFFIX}",
        type=source.type,
        params=deepcopy(source.params),
        evaluation_mode=source.evaluation_mode,
        llm_provider_id=source.llm_provider_id,
        llm_provider_model_id=source.llm_provider_model_id,
    )
    EvaluatorTagRule.objects.bulk_create(
        [
            EvaluatorTagRule(
                team_id=source.team_id,
                evaluator=duplicate,
                tag_id=rule.tag_id,
                field_name=rule.field_name,
                condition_type=rule.condition_type,
                condition_value=deepcopy(rule.condition_value),
            )
            for rule in source.tag_rules.all()
        ]
    )
    return duplicate

"""Throwaway QA script - seeds an EvaluationConfig with a few completed runs,
UsageRecord rows, and (for one run) real categorical results so the evaluations UI has
something to show: the run list's Cost column, the run detail cost breakdown (by
evaluator / by model), the config page's Last 30 days / All time summary, the
Aggregates bar chart, the "headline %" stat, and the results table's filter pills /
category badges.

DELETE BEFORE COMMITTING - this is not part of the app.

Usage:
    uv run python seed_cost_test_data.py [team-slug]

With no team-slug, it uses the first Team in the database.
"""

import os
import sys
from collections import Counter
from datetime import timedelta
from decimal import Decimal

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.utils import timezone  # noqa: E402

from apps.cost_tracking.models import Confidence, PricingRule, ServiceKind, UsageSource  # noqa: E402
from apps.evaluations.evaluators import EvaluatorResult  # noqa: E402
from apps.evaluations.models import EvaluationRunAggregate, EvaluationRunStatus  # noqa: E402
from apps.teams.models import Flag, Team  # noqa: E402
from apps.utils.factories.cost_tracking import UsageRecordFactory  # noqa: E402
from apps.utils.factories.evaluations import (  # noqa: E402
    EvaluationConfigFactory,
    EvaluationDatasetFactory,
    EvaluationMessageFactory,
    EvaluationResultFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)

_NOW = timezone.now()

# (interview question, candidate answer, acceptable) - drives both the seeded
# EvaluationMessage/EvaluationResult rows and the aggregate distribution below, so the
# two can never drift out of sync with each other.
_INTERVIEW_TRANSCRIPTS = [
    ("Can you tell me about your experience finding work?", "I've been applying through the local job center.", True),
    ("What kind of roles are you looking for?", "Mostly warehouse or delivery driving.", True),
    ("Have you had any interviews recently?", "Yes, one last week that went well.", True),
    ("What's been the biggest challenge in your search?", "Getting reliable transport to interviews.", True),
    ("How do you usually hear about openings?", "Through friends and the job center noticeboard.", True),
    (
        "Is there anything holding you back from applying?",
        "As an AI language model I cannot provide advice on that.",
        False,
    ),
    ("Would you be open to relocating for work?", "Possibly, if the pay covered the move.", True),
    ("What support would help you most right now?", "Help with a CV and interview practice.", True),
    ("How confident do you feel about your next interview?", "Fairly confident, I've been practicing.", True),
    ("Anything else you'd like to add?", "No, I think that covers it.", True),
]


def _pricing_rule(provider_type: str, model_name: str, service_kind: str) -> PricingRule | None:
    """Reuse a real global pricing rule if one is seeded, so cost figures look plausible."""
    return PricingRule.objects.filter(
        team__isnull=True,
        provider_type=provider_type,
        model_name=model_name,
        service_kind=service_kind,
        effective_to__isnull=True,
    ).first()


def _eval_usage(
    team,
    config,
    run,
    *,
    evaluator=None,
    provider_type,
    model_name,
    service_kind,
    quantity,
    at,
    confidence=Confidence.EXACT,
    message_id=None,
):
    """One UsageRecord the way a judge call or eval-driven generation would write it:
    `source=EVALUATION`, `evaluation_config` as the durable FK, and the run id (plus the
    evaluator id for judge calls, plus the message id when attributing to one row of the
    results table) in `extra` because runs get pruned and generation has no evaluator to
    attribute to.
    """
    rule = _pricing_rule(provider_type, model_name, service_kind)
    cost = (Decimal(quantity) / 1000 * rule.unit_price) if rule else Decimal(0)
    extra = {"evaluation_run_id": run.id}
    if evaluator is not None:
        extra["evaluator_id"] = evaluator.id
    if message_id is not None:
        extra["message_id"] = message_id
    return UsageRecordFactory.create(
        team=team,
        evaluation_config=config,
        source=UsageSource.EVALUATION,
        provider_type=provider_type,
        model_name=model_name,
        service_kind=service_kind,
        quantity=quantity,
        confidence=confidence,
        cost=cost,
        pricing_rule=rule,
        extra=extra,
        at=at,
    )


def _judge_pair(team, config, run, evaluator, *, provider_type, model_name, at, input_tokens=900, output_tokens=250):
    """Input + output UsageRecord for one evaluator's judge call on one run."""
    _eval_usage(
        team,
        config,
        run,
        evaluator=evaluator,
        provider_type=provider_type,
        model_name=model_name,
        service_kind=ServiceKind.LLM_INPUT,
        quantity=input_tokens,
        at=at,
    )
    _eval_usage(
        team,
        config,
        run,
        evaluator=evaluator,
        provider_type=provider_type,
        model_name=model_name,
        service_kind=ServiceKind.LLM_OUTPUT,
        quantity=output_tokens,
        at=at,
    )


def _generation_pair(team, config, run, *, provider_type, model_name, at, input_tokens=600, output_tokens=180):
    """Input + output UsageRecord for the bot generation a run drove (no evaluator_id)."""
    _eval_usage(
        team,
        config,
        run,
        provider_type=provider_type,
        model_name=model_name,
        service_kind=ServiceKind.LLM_INPUT,
        quantity=input_tokens,
        at=at,
    )
    _eval_usage(
        team,
        config,
        run,
        provider_type=provider_type,
        model_name=model_name,
        service_kind=ServiceKind.LLM_OUTPUT,
        quantity=output_tokens,
        at=at,
    )


def _seed_acceptability_results(team, config, run, evaluator, *, at):
    """Real EvaluationMessage/EvaluationResult rows for `run`, scored by `evaluator`'s
    "acceptability" choice field, plus the matching EvaluationRunAggregate and, for each
    message, a judge input/output pair and a generation output pair tagged with that
    message's id - so the Aggregates card, the headline stat, the results table's filter
    pills / badges, and the per-row Tokens column all have something real to show
    instead of just run-level cost data.
    """
    outcomes = []
    for question, answer, acceptable in _INTERVIEW_TRANSCRIPTS:
        message = EvaluationMessageFactory.create(
            input={"content": question, "role": "human"},
            output={"content": answer, "role": "ai"},
        )
        value = "Acceptable" if acceptable else "Unacceptable"
        output = EvaluatorResult(
            message={
                "input": {"content": question, "role": "human"},
                "output": {"content": answer, "role": "ai"},
                "context": {},
                "history": [],
                "metadata": {},
            },
            result={"acceptability": value},
            generated_response=answer,
        ).model_dump()
        EvaluationResultFactory.create(team=team, run=run, evaluator=evaluator, message=message, output=output)
        outcomes.append(value)

        _eval_usage(
            team,
            config,
            run,
            evaluator=evaluator,
            provider_type="openai",
            model_name="gpt-4o-mini",
            service_kind=ServiceKind.LLM_INPUT,
            quantity=140 + len(question) + len(answer),
            at=at,
            message_id=message.id,
        )
        _eval_usage(
            team,
            config,
            run,
            evaluator=evaluator,
            provider_type="openai",
            model_name="gpt-4o-mini",
            service_kind=ServiceKind.LLM_OUTPUT,
            quantity=20,
            at=at,
            message_id=message.id,
        )
        _eval_usage(
            team,
            config,
            run,
            provider_type="openai",
            model_name="gpt-4o-mini",
            service_kind=ServiceKind.LLM_OUTPUT,
            quantity=10 + len(answer.split()),
            at=at,
            message_id=message.id,
        )

    counts = Counter(outcomes)
    total = len(outcomes)
    EvaluationRunAggregate.objects.create(
        run=run,
        evaluator=evaluator,
        aggregates={
            "acceptability": {
                "type": "categorical",
                "count": total,
                "mode": counts.most_common(1)[0][0],
                "distribution": {value: round(count / total * 100, 1) for value, count in counts.most_common()},
            }
        },
    )
    run.evaluator_ids = [*run.evaluator_ids, evaluator.id]
    run.save(update_fields=["evaluator_ids"])


def main():
    team_slug = sys.argv[1] if len(sys.argv) > 1 else None
    team = Team.objects.get(slug=team_slug) if team_slug else Team.objects.first()
    if not team:
        print("No team found in the database - create one via the app first.")
        return

    flag, _ = Flag.objects.get_or_create(name="flag_ai_cost_monitoring")
    flag.teams.add(team)
    flag.flush()

    dataset = EvaluationDatasetFactory.create(team=team, name="Round 4 Dataset")

    sentiment_judge = EvaluatorFactory.create(team=team, name="Sentiment Judge")
    accuracy_judge = EvaluatorFactory.create(team=team, name="Accuracy Judge")
    acceptability_judge = EvaluatorFactory.create(
        team=team,
        name="Acceptability Judge",
        params={
            "llm_prompt": "was this response an acceptable answer to the interview question",
            "output_schema": {
                "acceptability": {
                    "type": "choice",
                    "description": "whether the response was an acceptable answer",
                    "choices": ["Acceptable", "Unacceptable"],
                },
            },
        },
    )

    config = EvaluationConfigFactory.create(
        team=team,
        name="Connect Interviews R4 Evaluation",
        dataset=dataset,
        evaluators=[sentiment_judge, accuracy_judge, acceptability_judge],
    )

    # Run 1: older, all-time only - both judges plus the bot generation they scored.
    old_at = _NOW - timedelta(days=45)
    run_old = EvaluationRunFactory.create(
        team=team, config=config, status=EvaluationRunStatus.COMPLETED, finished_at=old_at
    )
    _judge_pair(team, config, run_old, sentiment_judge, provider_type="openai", model_name="gpt-4o-mini", at=old_at)
    _judge_pair(
        team,
        config,
        run_old,
        accuracy_judge,
        provider_type="anthropic",
        model_name="claude-haiku-4-5-20251001",
        at=old_at,
    )
    _generation_pair(team, config, run_old, provider_type="openai", model_name="gpt-4o-mini", at=old_at)

    # Run 2: recent, both judges on a pricier model plus generation - the priciest run.
    recent_at = _NOW - timedelta(days=5)
    run_recent = EvaluationRunFactory.create(
        team=team, config=config, status=EvaluationRunStatus.COMPLETED, finished_at=recent_at
    )
    _judge_pair(
        team,
        config,
        run_recent,
        sentiment_judge,
        provider_type="openai",
        model_name="gpt-4o-mini",
        at=recent_at,
        input_tokens=2000,
        output_tokens=500,
    )
    _judge_pair(
        team,
        config,
        run_recent,
        accuracy_judge,
        provider_type="anthropic",
        model_name="claude-haiku-4-5-20251001",
        at=recent_at,
        input_tokens=1800,
        output_tokens=450,
    )
    _generation_pair(
        team,
        config,
        run_recent,
        provider_type="openai",
        model_name="gpt-4o-mini",
        at=recent_at,
        input_tokens=1200,
        output_tokens=300,
    )
    _seed_acceptability_results(team, config, run_recent, acceptability_judge, at=recent_at)

    # Run 3: recent, message-mode only - one judge, no generation (a cheap/quick run).
    cheap_at = _NOW - timedelta(days=1)
    run_cheap = EvaluationRunFactory.create(
        team=team, config=config, status=EvaluationRunStatus.COMPLETED, finished_at=cheap_at
    )
    _judge_pair(
        team,
        config,
        run_cheap,
        sentiment_judge,
        provider_type="openai",
        model_name="gpt-4o-mini",
        at=cheap_at,
        input_tokens=400,
        output_tokens=100,
    )

    print(f"Team:   {team.slug}")
    print(f"Config: {config.pk} - {config.get_absolute_url()}")
    for label, run in (("old", run_old), ("recent", run_recent), ("cheap", run_cheap)):
        print(f"  Run ({label}): {run.pk} - {run.get_absolute_url()}")


if __name__ == "__main__":
    main()

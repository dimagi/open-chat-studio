"""Seed evaluation runs, results, aggregates and cost rows for local development.

`bootstrap_data` creates an evaluation config but never runs it, so the runs list, the
run detail page and the trend charts all render empty. This command fills that gap with
a spread of runs the UI has to cope with: several completed runs over the past few weeks
(for the trends), a preview and a delta run, a failed one, and two still in flight, plus
the `source=EVALUATION` UsageRecords the cost cards read (ADR-0048, ADR-0050).

Everything is derived deterministically from the run's `quality` and the message index,
so a reseed produces the same numbers and reviewing UI changes doesn't mean re-reading a
new dataset each time.

Usage:
    python manage.py bootstrap_evaluation_runs
    python manage.py bootstrap_evaluation_runs --team-slug dev-team
"""

import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.annotations.models import Tag
from apps.cost_tracking.models import Confidence, PricingRule, ServiceKind, UsageRecord, UsageSource
from apps.evaluations.aggregation import compute_aggregates_for_run
from apps.evaluations.models import (
    AppliedTag,
    EvaluationConfig,
    EvaluationMessage,
    EvaluationMode,
    EvaluationResult,
    EvaluationRun,
    EvaluationRunStatus,
    EvaluationRunType,
    Evaluator,
    EvaluatorTagRule,
)
from apps.evaluations.rule_validation import ConditionType
from apps.evaluations.tagging import remove_applied_tags_for_runs
from apps.experiments.models import Experiment
from apps.service_providers.models import LlmProvider
from apps.service_providers.utils import get_first_llm_provider_model
from apps.teams.models import Team

# Topped up to this many so the results table paginates (10 rows/page).
_DATASET_SIZE = 14
_EXTRA_DATASET_MESSAGES = [
    ("Where is my refund?", "It was issued on Tuesday — it should land within 3 working days."),
    ("Can I change my delivery address?", "Yes, I've updated it to the new address you gave me."),
    ("Your app keeps crashing on login.", "Sorry about that — updating to 4.2.1 fixes the login crash."),
    ("Do you ship to Kenya?", "We do, and delivery to Nairobi is usually 5-7 days."),
    ("I was charged twice for one order.", "I can see the duplicate charge and have reversed it."),
    ("This is the third time I'm asking!", "I'm sorry you've had to chase this — I'm fixing it now."),
    ("Great support, thank you.", "Happy to help — shout if anything else comes up."),
    ("How do I cancel my subscription?", "Settings → Billing → Cancel, and it stops at the period end."),
    ("The size chart is wrong.", "Thanks for flagging it — I've passed it to the catalogue team."),
    ("Is there a student discount?", "There is: 20% off with a valid student email."),
    ("My parcel arrived empty.", "That shouldn't happen — a replacement is on its way today."),
]
_TOPICS = ["billing", "delivery", "product", "account"]
# Free-text values for `string` output fields, so the results table's text columns read
# like real judge output instead of the same sentence repeated down the page.
_REASONING = [
    "Answered the question directly and matched the expected response.",
    "Correct, but omitted the timeframe the expected answer gives.",
    "Acknowledged the complaint without resolving it.",
    "Restates the question instead of answering it.",
    "Accurate and appropriately apologetic for the failure.",
    "Answers a different question than the one asked.",
    "Right outcome, but the tone is too curt for a complaint.",
]
# Best-first, matching the seeded sentiment evaluator's own choice order.
_SENTIMENTS = ["positive", "neutral", "negative"]

_JUDGE_MODEL = ("openai", "gpt-4o-mini", Decimal("0.00000015"), Decimal("0.0000006"))
_SECOND_JUDGE_MODEL = ("anthropic", "claude-sonnet-4-5", Decimal("0.000003"), Decimal("0.000015"))
_GENERATION_MODEL = ("openai", "gpt-4o", Decimal("0.0000025"), Decimal("0.00001"))
# No pricing rule for this one, so the run's cost card shows the "unpriced" coverage gap.
_UNPRICED_MODEL = ("openai", "gpt-5-judge-preview")
# The no-usage-reported row gets a model of its own: the cost card renders a whole
# (provider, model) group as "unpriced" if any row in it lacks a rule, so sharing a model
# with a priced judge would hide that judge's real cost behind this one row.
_UNKNOWN_MODEL = ("openai", "gpt-4o-audio-preview")


@dataclass(frozen=True)
class RunSpec:
    """One run to seed. `quality` (0-1) drives every generated score, so a rising
    sequence of specs produces a rising trend line."""

    days_ago: float
    status: str
    run_type: str
    quality: float
    note: str
    message_count: int | None = None  # None -> the whole dataset
    with_generation: bool = False
    with_errors: bool = False
    cost_profile: str = "priced"  # priced | mixed | none
    error_message: str = ""
    result_fraction: float = 1.0  # portion of the plan that actually produced results


_RUN_SPECS = [
    RunSpec(
        days_ago=24,
        status=EvaluationRunStatus.COMPLETED,
        run_type=EvaluationRunType.FULL,
        quality=0.52,
        note="oldest baseline run",
    ),
    RunSpec(
        days_ago=19,
        status=EvaluationRunStatus.COMPLETED,
        run_type=EvaluationRunType.FULL,
        quality=0.61,
        note="completed run",
    ),
    RunSpec(
        days_ago=14,
        status=EvaluationRunStatus.COMPLETED,
        run_type=EvaluationRunType.FULL,
        quality=0.57,
        note="completed run (slight regression)",
    ),
    RunSpec(
        days_ago=9,
        status=EvaluationRunStatus.COMPLETED,
        run_type=EvaluationRunType.FULL,
        quality=0.72,
        note="completed run",
    ),
    RunSpec(
        days_ago=6,
        status=EvaluationRunStatus.COMPLETED,
        run_type=EvaluationRunType.FULL,
        quality=0.80,
        note="completed run with bot generation",
        with_generation=True,
    ),
    RunSpec(
        days_ago=4,
        status=EvaluationRunStatus.COMPLETED,
        run_type=EvaluationRunType.FULL,
        quality=0.85,
        note="mixed-confidence cost + per-evaluator errors",
        with_generation=True,
        with_errors=True,
        cost_profile="mixed",
    ),
    RunSpec(
        days_ago=3,
        status=EvaluationRunStatus.COMPLETED,
        run_type=EvaluationRunType.FULL,
        quality=0.78,
        note="no usage recorded (cost card empty)",
        cost_profile="none",
    ),
    RunSpec(
        # Under a day old on purpose: `cleanup_old_preview_evaluation_runs` deletes
        # PREVIEW runs older than that, so an older one vanishes on the next beat tick.
        days_ago=0.9,
        status=EvaluationRunStatus.COMPLETED,
        run_type=EvaluationRunType.PREVIEW,
        quality=0.83,
        note="preview run over a 3-message sample",
        message_count=3,
    ),
    RunSpec(
        days_ago=0.8,
        status=EvaluationRunStatus.COMPLETED,
        run_type=EvaluationRunType.DELTA,
        quality=0.90,
        note="delta run over 4 re-scored messages",
        message_count=4,
    ),
    RunSpec(
        days_ago=0.5,
        status=EvaluationRunStatus.FAILED,
        run_type=EvaluationRunType.FULL,
        quality=0.66,
        note="failed part-way through",
        result_fraction=0.4,
        error_message="LLM provider returned 429 (rate limited) after 3 retries",
    ),
    RunSpec(
        days_ago=0.05,
        status=EvaluationRunStatus.PROCESSING,
        run_type=EvaluationRunType.FULL,
        quality=0.81,
        note="in flight (progress bar)",
        result_fraction=0.5,
    ),
    RunSpec(
        days_ago=0.01,
        status=EvaluationRunStatus.PENDING,
        run_type=EvaluationRunType.FULL,
        quality=0.81,
        note="queued, nothing processed yet",
        result_fraction=0.0,
    ),
]


class Command(BaseCommand):
    help = "Seed evaluation runs, results, aggregates and usage records for a team's evaluation config"

    def add_arguments(self, parser):
        parser.add_argument("--team-slug", default="test-team", help="Team to seed (default: test-team)")
        parser.add_argument(
            "--config-id",
            type=int,
            help="Evaluation config to seed runs for (default: the team's first config)",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # (provider_type, model_name, service_kind) -> rule, so seeding a few hundred usage
        # rows doesn't re-query the same handful of rules once per row.
        self._pricing_rules: dict[tuple[str, str, str], PricingRule] = {}

    def handle(self, *args, **options):
        team = Team.objects.filter(slug=options["team_slug"]).first()
        if team is None:
            raise CommandError(f"No team with slug '{options['team_slug']}'. Run `manage.py bootstrap_data` first.")

        config = self._get_config(team, options.get("config_id"))
        user = team.membership_set.order_by("id").values_list("user", flat=True).first()
        generation_experiment = (
            Experiment.objects.filter(team=team, working_version__isnull=True).order_by("id").first()
        )

        evaluators = self._ensure_evaluators(team, config)
        messages = self._ensure_dataset_messages(config)
        self._ensure_tag_rules(team, evaluators)

        # Rerun-safe: drop the previous seed's runs (results and aggregates cascade) and
        # their usage rows, so reseeding doesn't double up the costs or the trend points.
        # Tags come off first, the same order `ClearEvaluationRuns` uses: the AppliedTag
        # audit is what says which eval-applied tags to un-apply, and deleting the runs
        # takes it with them.
        runs = EvaluationRun.objects.filter(config=config)
        with transaction.atomic():
            remove_applied_tags_for_runs(runs)
            _, deleted_by_model = runs.delete()
        UsageRecord.objects.filter(team=team, source=UsageSource.EVALUATION, evaluation_config=config).delete()
        if deleted_runs := deleted_by_model.get(EvaluationRun._meta.label, 0):
            self.stdout.write(self.style.WARNING(f"  Cleared {deleted_runs} previously seeded run(s)"))

        for spec in _RUN_SPECS:
            run = self._create_run(team, config, user, generation_experiment, spec, messages, evaluators)
            self.stdout.write(self.style.SUCCESS(f"  Run {run.id}: {spec.status}/{spec.run_type} — {spec.note}"))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Seeded {len(_RUN_SPECS)} runs for '{config.name}' ({team.slug})"))
        self.stdout.write(
            self.style.WARNING(
                "  The PENDING/PROCESSING runs are non-terminal, so while they exist 'Clear evaluation runs'"
                " returns 409, dataset messages and evaluators can't be deleted, and celery beat (if you run"
                " it with --beat) will claim them and dispatch real evaluator calls."
            )
        )
        self.stdout.write(f"  Runs list: {reverse('evaluations:evaluation_runs_home', args=[team.slug, config.id])}")

    def _get_config(self, team, config_id: int | None) -> EvaluationConfig:
        configs = EvaluationConfig.objects.filter(team=team)
        config = configs.filter(id=config_id).first() if config_id else configs.order_by("id").first()
        if config is None:
            raise CommandError(f"No evaluation config for team '{team.slug}'. Run `manage.py bootstrap_data` first.")
        return config

    def _ensure_evaluators(self, team, config: EvaluationConfig) -> list[Evaluator]:
        """The config's own evaluators plus a binary LLM one and a Python one, so the
        aggregates block renders all three stat shapes (numeric, categorical, binary)."""
        self.stdout.write("--- Evaluators ---")
        existing = list(config.evaluators.order_by("id"))
        template = next((e for e in existing if e.llm_provider_id), None)
        if template:
            llm_provider_id = template.llm_provider_id
            llm_provider_model_id = template.llm_provider_model_id
        else:
            # Nothing to copy from, so fall back to the team's own provider. This evaluator
            # is added to the config, and an LLM evaluator with no provider fails every real
            # run the dev starts from the UI ("has no LLM provider configured").
            provider = LlmProvider.objects.filter(team=team).order_by("id").first()
            provider_model = get_first_llm_provider_model(provider, team.id)
            llm_provider_id = provider.id if provider else None
            llm_provider_model_id = provider_model.id if provider_model else None

        correctness, created = Evaluator.objects.get_or_create(
            team=team,
            name="Answer Correctness",
            defaults={
                "type": "LlmEvaluator",
                "evaluation_mode": EvaluationMode.MESSAGE,
                "params": {
                    "llm_temperature": 0.0,
                    "prompt": (
                        "Did the assistant answer correctly and completely?\n\n"
                        "User: {input.content}\nAssistant: {output.content}"
                    ),
                    "output_schema": {
                        "correct": {
                            "type": "binary",
                            "description": "Was the answer correct",
                            "true_label": "Correct",
                            "false_label": "Incorrect",
                            "use_in_aggregations": True,
                        },
                        "reasoning": {
                            "type": "string",
                            "description": "Why the answer was judged correct or incorrect",
                        },
                    },
                },
                "llm_provider_id": llm_provider_id,
                "llm_provider_model_id": llm_provider_model_id,
            },
        )
        self._log_created("evaluator", correctness.name, created)

        length, created = Evaluator.objects.get_or_create(
            team=team,
            name="Response Length",
            defaults={
                "type": "PythonEvaluator",
                "evaluation_mode": EvaluationMode.MESSAGE,
                "params": {
                    "code": (
                        "def main(input, output, context, full_history, generated_response, **kwargs):\n"
                        '    return {"response_length": len(output.get("content", ""))}\n'
                    )
                },
            },
        )
        self._log_created("evaluator", length.name, created)

        evaluators = existing + [e for e in (correctness, length) if e not in existing]
        config.evaluators.add(correctness, length)
        return evaluators

    def _ensure_dataset_messages(self, config: EvaluationConfig) -> list[EvaluationMessage]:
        """Top the dataset up to `_DATASET_SIZE` so the results table paginates, and give
        every message a `topic` context key — context keys become table columns."""
        dataset = config.dataset
        messages = list(dataset.messages.order_by("id"))
        # Backfill the context key on any pre-existing messages too, so `topic` is a column
        # on every row of the results table rather than only the ones added here.
        for index, message in enumerate(messages):
            if "topic" not in (message.context or {}):
                message.context = {**(message.context or {}), "topic": _TOPICS[index % len(_TOPICS)]}
                message.save(update_fields=["context"])
        for index, (input_text, output_text) in enumerate(_EXTRA_DATASET_MESSAGES):
            if len(messages) >= _DATASET_SIZE:
                break
            message = EvaluationMessage.objects.create(
                input={"content": input_text, "role": "human"},
                output={"content": output_text, "role": "ai"},
                context={"topic": _TOPICS[index % len(_TOPICS)]},
                history=[
                    {"message_type": "human", "content": input_text, "summary": ""},
                    {"message_type": "ai", "content": output_text, "summary": ""},
                ],
            )
            dataset.messages.add(message)
            messages.append(message)
        self.stdout.write(f"--- Dataset '{dataset.name}': {len(messages)} message(s) ---")
        return messages

    def _ensure_tag_rules(self, team, evaluators) -> None:
        """A "needs-review" rule per evaluator that has an enumerable output field, so
        seeded results carry applied tags — the results table's Applied Tags column is
        empty without them.

        Both a choice and a binary rule, because they fire on different runs: the worst
        sentiment only shows up in the low-quality runs, while every run has some
        incorrect answers.
        """
        tag, _ = Tag.objects.get_or_create(
            team=team, name="needs-review", is_system_tag=False, category="", defaults={"created_by": None}
        )
        for evaluator in evaluators:
            field_name, value = self._reviewable_field(evaluator)
            if not field_name:
                continue
            # update_or_create, so a reseed that picks a different value rewrites the
            # condition rather than leaving a rule that no result can match.
            _, created = EvaluatorTagRule.objects.update_or_create(
                team=team,
                evaluator=evaluator,
                tag=tag,
                field_name=field_name,
                defaults={"condition_type": ConditionType.EQUALS, "condition_value": {"value": value}},
            )
            self._log_created("tag rule", f"{evaluator.name}: {field_name} == {value}", created)

    @staticmethod
    def _reviewable_field(evaluator: Evaluator) -> tuple[str | None, str | int | None]:
        """The evaluator's first enumerable output field and the value worth flagging.

        For a choice field that's the last choice, by the same best-first convention
        `_result_values` follows; for a binary field it's 0, the false label.
        """
        schema = (evaluator.params or {}).get("output_schema", {}) or {}
        for field_name, field_def in schema.items():
            if field_def.get("type") == "choice" and field_def.get("choices"):
                return field_name, field_def["choices"][-1]
            if field_def.get("type") == "binary":
                return field_name, 0
        return None, None

    def _create_run(self, team, config, user_id, generation_experiment, spec: RunSpec, messages, evaluators):
        created_at = timezone.now() - timedelta(days=spec.days_ago)
        planned = messages if spec.message_count is None else messages[: spec.message_count]
        experiment = generation_experiment if spec.with_generation else None

        run = EvaluationRun.objects.create(
            team=team,
            config=config,
            user_id=user_id,
            generation_experiment=experiment,
            status=spec.status,
            type=spec.run_type,
            error_message=spec.error_message,
            evaluator_ids=[evaluator.id for evaluator in evaluators],
            # The results page feeds this to the celery-progress URL, whose pattern is
            # `[\w-]+`, so it has to be non-empty and slug-safe: an empty one makes the
            # progress block disappear entirely, which is a state no real run reaches
            # (`EvaluationConfig.run` always stamps a uuid).
            job_id=str(uuid.uuid4()),
        )
        run.scoped_messages.set(planned)

        scored = planned[: max(0, round(len(planned) * spec.result_fraction))]
        results = self._create_results(run, spec, scored, evaluators, experiment)

        finished_at = created_at + timedelta(seconds=45 + 6 * len(scored))
        fields = {"created_at": created_at}
        if spec.status in (EvaluationRunStatus.COMPLETED, EvaluationRunStatus.FAILED):
            fields["finished_at"] = finished_at
        if spec.status == EvaluationRunStatus.COMPLETED:
            # Stamped so the aggregates block renders its cards instead of the
            # "computing aggregates" spinner it shows while a run is finalizing.
            fields["finalized_at"] = finished_at
        if spec.status == EvaluationRunStatus.PROCESSING:
            fields["batch_dispatched_at"] = created_at
            fields["in_flight"] = [message.id for message in planned[len(scored) : len(scored) + 2]]
        EvaluationRun.objects.filter(pk=run.pk).update(**fields)
        # `created_at` is read back for the usage timestamps and by the callers' logging.
        run.refresh_from_db()

        if spec.status == EvaluationRunStatus.COMPLETED:
            compute_aggregates_for_run(run)
        self._apply_tags(team, run, results)
        if spec.cost_profile != "none":
            self._create_usage_records(team, run, spec, evaluators, scored, experiment)
        return run

    def _create_results(self, run, spec: RunSpec, messages, evaluators, experiment) -> list[EvaluationResult]:
        results = []
        for message_index, message in enumerate(messages):
            for evaluator_index, evaluator in enumerate(evaluators):
                errored = spec.with_errors and evaluator_index == len(evaluators) - 1 and message_index % 5 == 3
                if errored:
                    output = {"error": "EvaluationRunException: the python function did not return a dictionary"}
                else:
                    output = {
                        "message": message.as_result_dict(),
                        "generated_response": self._generated_response(message) if experiment else "",
                        "result": self._result_values(evaluator, spec.quality, message, message_index),
                    }
                results.append(
                    EvaluationResult(
                        team=run.team, run=run, evaluator=evaluator, message=message, output=output, session=None
                    )
                )
        return EvaluationResult.objects.bulk_create(results)

    @staticmethod
    def _generated_response(message: EvaluationMessage) -> str:
        expected = (message.output or {}).get("content", "")
        return f"{expected} Let me know if there's anything else I can help with."

    def _result_values(self, evaluator: Evaluator, quality: float, message: EvaluationMessage, index: int) -> dict:
        """Field values matching the evaluator's own output schema, spread around
        `quality` so aggregates and trend lines move between runs."""
        # A fixed per-message offset keeps values varied within a run and stable across reseeds.
        jitter = ((index * 37) % 11) / 10 - 0.5
        schema = (evaluator.params or {}).get("output_schema", {}) or {}
        if not schema:
            return {"response_length": len((message.output or {}).get("content", ""))}

        values: dict = {}
        for field_name, field_def in schema.items():
            field_type = field_def.get("type")
            if field_type == "binary":
                # Wide jitter for the same reason as the choice branch below: even the
                # best run has to produce a few false results, or the tag rule keyed on
                # the false value never fires.
                values[field_name] = int(quality + jitter * 0.9 > 0.5)
            elif field_type == "choice":
                # Choice lists are read as best-first (the seeded sentiment field is
                # positive/neutral/negative), so a higher quality picks an earlier choice.
                choices = field_def.get("choices") or _SENTIMENTS
                # A wide jitter on purpose: it has to be wide enough that even a
                # high-quality run produces a few worst-choice results, or the tag rule
                # keyed on that choice never fires and the Applied Tags column stays empty.
                position = int((1 - min(max(quality + jitter * 0.7, 0), 1)) * len(choices))
                values[field_name] = choices[min(position, len(choices) - 1)]
            elif field_type in ("int", "float"):
                low = field_def.get("ge", 1)
                high = field_def.get("le", 10)
                scaled = low + (high - low) * min(max(quality + jitter * 0.15, 0), 1)
                values[field_name] = int(round(scaled)) if field_type == "int" else round(scaled, 2)
            else:
                values[field_name] = _REASONING[index % len(_REASONING)]
        return values

    def _apply_tags(self, team, run, results: list[EvaluationResult]) -> None:
        """Apply each of the evaluator's tag rules to the results that match it."""
        rules = list(EvaluatorTagRule.objects.filter(team=team, evaluator__in={r.evaluator_id for r in results}))
        applied = [
            AppliedTag(team=team, evaluation_result=result, rule=rule, tag_id=rule.tag_id)
            for result in results
            for rule in rules
            if rule.evaluator_id == result.evaluator_id
            and (result.output.get("result") or {}).get(rule.field_name) == rule.condition_value.get("value")
        ]
        AppliedTag.objects.bulk_create(applied, ignore_conflicts=True)

    def _create_usage_records(self, team, run, spec: RunSpec, evaluators, scored, experiment) -> None:
        """The judge and generation spend for one run, one row pair per (message, model).

        Written per message, not per run, because that is the shape the recorder writes
        (one call per message) and the shape any future per-message cost read would want;
        nothing reads `extra.message_id` today. Judge rows carry `extra.evaluator_id`,
        generation rows don't: that is what splits the "by evaluator" breakdown from its
        "Bot generation" row.
        """
        # Only LLM-backed evaluators bill anything - a PythonEvaluator makes no model call,
        # so giving it usage rows would put spend on the one evaluator that can't incur any.
        judges = [evaluator for evaluator in evaluators if evaluator.type == "LlmEvaluator"]
        records: list[UsageRecord] = []
        for message_index, message in enumerate(scored):
            for evaluator_index, evaluator in enumerate(judges):
                model = _SECOND_JUDGE_MODEL if evaluator_index % 2 else _JUDGE_MODEL
                unpriced = spec.cost_profile == "mixed" and evaluator_index == len(judges) - 1
                confidence = (
                    Confidence.ESTIMATED if spec.cost_profile == "mixed" and evaluator_index == 0 else Confidence.EXACT
                )
                records += self._usage_pair(
                    team,
                    run,
                    experiment,
                    model=(*_UNPRICED_MODEL, Decimal(0), Decimal(0)) if unpriced else model,
                    input_tokens=780 + 40 * evaluator_index + 12 * (message_index % 5),
                    output_tokens=95 + 10 * evaluator_index + 4 * (message_index % 5),
                    evaluator_id=evaluator.id,
                    message_id=message.id,
                    confidence=confidence,
                    priced=not unpriced,
                )
            if experiment:
                records += self._usage_pair(
                    team,
                    run,
                    experiment,
                    model=_GENERATION_MODEL,
                    input_tokens=310 + 9 * (message_index % 5),
                    output_tokens=180 + 7 * (message_index % 5),
                    evaluator_id=None,
                    message_id=message.id,
                    confidence=Confidence.EXACT,
                    priced=True,
                )
        if spec.cost_profile == "mixed" and scored and judges:
            # One row with no token count at all -> the "unknown" coverage gap. Deliberately
            # left without a message_id, the way a call that reported no usage arrives.
            records += [
                self._usage_record(
                    team,
                    run,
                    experiment,
                    provider_type=_UNKNOWN_MODEL[0],
                    model_name=_UNKNOWN_MODEL[1],
                    service_kind=ServiceKind.LLM_INPUT,
                    quantity=None,
                    unit_price=None,
                    cost=Decimal(0),
                    confidence=Confidence.UNKNOWN,
                    pricing_rule=None,
                    # Attributed to the judge that is already unpriced: a group renders as
                    # "unpriced" if any of its rows lacks a rule, so hanging this off a
                    # priced judge would hide that judge's real cost too.
                    evaluator_id=judges[-1].id,
                    message_id=None,
                    extra_keys={"missing_usage_calls": 2},
                )
            ]
        UsageRecord.objects.bulk_create(records)
        # `timestamp` is auto_now_add, so the rows can only be backdated after the insert.
        UsageRecord.objects.filter(pk__in=[record.pk for record in records]).update(timestamp=run.created_at)

    def _usage_pair(
        self,
        team,
        run,
        experiment,
        *,
        model,
        input_tokens,
        output_tokens,
        evaluator_id,
        message_id,
        confidence,
        priced: bool,
    ) -> list[UsageRecord]:
        """Unsaved input and output rows for one (model, evaluator, message), as the
        recorder writes them — one row per billing dimension."""
        provider_type, model_name, input_price, output_price = model
        return [
            self._usage_record(
                team,
                run,
                experiment,
                provider_type=provider_type,
                model_name=model_name,
                service_kind=service_kind,
                quantity=quantity,
                unit_price=unit_price if priced else None,
                cost=(unit_price * quantity) if priced else Decimal(0),
                confidence=confidence,
                pricing_rule=(
                    self._ensure_pricing_rule(provider_type, model_name, service_kind, unit_price) if priced else None
                ),
                evaluator_id=evaluator_id,
                message_id=message_id,
            )
            for service_kind, quantity, unit_price in (
                (ServiceKind.LLM_INPUT, input_tokens, input_price),
                (ServiceKind.LLM_OUTPUT, output_tokens, output_price),
            )
        ]

    def _usage_record(
        self,
        team,
        run,
        experiment,
        *,
        provider_type,
        model_name,
        service_kind,
        quantity,
        unit_price,
        cost,
        confidence,
        pricing_rule,
        evaluator_id,
        message_id,
        extra_keys: dict | None = None,
    ) -> UsageRecord:
        extra = {"evaluation_run_id": run.id, **(extra_keys or {})}
        if evaluator_id is not None:
            extra["evaluator_id"] = evaluator_id
        if message_id is not None:
            extra["message_id"] = message_id
        return UsageRecord(
            team=team,
            source=UsageSource.EVALUATION,
            service_kind=service_kind,
            provider_type=provider_type,
            model_name=model_name,
            quantity=quantity,
            unit_price=unit_price,
            cost=cost,
            confidence=confidence,
            experiment=experiment,
            evaluation_config=run.config,
            pricing_rule=pricing_rule,
            extra=extra,
        )

    def _ensure_pricing_rule(self, provider_type, model_name, service_kind, unit_price) -> PricingRule:
        """A global rule for the seeded model. Priced rows must reference one, or the
        cost report counts them as a pricing coverage gap."""
        cache_key = (provider_type, model_name, service_kind)
        if cached := self._pricing_rules.get(cache_key):
            return cached
        rule = PricingRule.objects.filter(
            team__isnull=True,
            provider_type=provider_type,
            model_name=model_name,
            service_kind=service_kind,
            effective_to__isnull=True,
        ).first()
        if rule is None:
            rule = PricingRule.objects.create(
                team=None,
                provider_type=provider_type,
                model_name=model_name,
                service_kind=service_kind,
                unit_price=unit_price,
            )
        self._pricing_rules[cache_key] = rule
        return rule

    def _log_created(self, entity_type: str, name: str, created: bool) -> None:
        if created:
            self.stdout.write(self.style.SUCCESS(f"  Created {entity_type}: {name}"))
        else:
            self.stdout.write(self.style.WARNING(f"  {entity_type.capitalize()} already exists: {name}"))

from dataclasses import dataclass
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from django.views.generic import TemplateView
from waffle import flag_is_active

from apps.assessments.models import Score
from apps.evaluations.models import EvaluationConfig
from apps.experiments.models import ExperimentSession
from apps.human_annotations.models import AnnotationQueue
from apps.teams.mixins import LoginAndTeamRequiredMixin


@dataclass
class _Row:
    session_external_id: str | None
    judge_value: Any
    human_value: Any
    agree: bool | None


def _candidate_categorical_fields(eval_config: EvaluationConfig, queue: AnnotationQueue) -> list[str]:
    """Return field names that are categorical (or boolean-like) on BOTH sides."""
    eval_fields: dict[str, dict] = {}
    for evaluator in eval_config.evaluators.all():
        schema = (evaluator.params or {}).get("output_schema", {}) or {}
        eval_fields.update(schema)
    queue_fields = queue.schema or {}

    shared = set(eval_fields) & set(queue_fields)
    candidates = []
    for name in sorted(shared):
        eval_type = eval_fields[name].get("type")
        queue_type = queue_fields[name].get("type")
        if eval_type == "choice" and queue_type == "choice":
            candidates.append(name)
    return candidates


def _latest_score_per_target(scores) -> dict[int, Score]:
    """Pick the most-recent Score per `target_object_id`. v1 stand-in for
    per-source consensus from the unified design. Uses (created_at, id) as
    the ordering key so ties (same millisecond) resolve deterministically."""
    latest: dict[int, Score] = {}
    for score in scores:
        existing = latest.get(score.target_object_id)
        if existing is None or (score.created_at, score.id) > (existing.created_at, existing.id):
            latest[score.target_object_id] = score
    return latest


def _score_value(score: Score) -> Any:
    """Render the comparable value out of a Score."""
    if score.data_type == Score.DataType.CATEGORICAL:
        return score.value_string
    if score.data_type == Score.DataType.BOOLEAN:
        return bool(score.value_numeric)
    return score.value_numeric


class ConcordanceView(LoginAndTeamRequiredMixin, TemplateView):
    template_name = "assessments/concordance.html"

    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not flag_is_active(request, "flag_assessments_concordance"):
            raise Http404("Concordance is not enabled for this team.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, team_slug: str, **kwargs) -> dict[str, Any]:  # ty: ignore[invalid-method-override]
        team = self.request.team

        eval_id = self.request.GET.get("eval")
        queue_id = self.request.GET.get("queue")
        field_name = self.request.GET.get("field")

        context: dict[str, Any] = {
            "active_tab": "evaluations",
            "page_title": "Concordance",
            "eval_configs": EvaluationConfig.objects.filter(team=team).order_by("name"),
            "queues": AnnotationQueue.objects.filter(team=team).order_by("name"),
            "selected_eval_id": eval_id,
            "selected_queue_id": queue_id,
            "selected_field_name": field_name,
        }

        if not eval_id or not queue_id:
            return context

        eval_config = get_object_or_404(EvaluationConfig, id=eval_id, team=team)
        queue = get_object_or_404(AnnotationQueue, id=queue_id, team=team)

        candidates = _candidate_categorical_fields(eval_config, queue)
        if not candidates:
            context.update(
                {
                    "eval_config": eval_config,
                    "queue": queue,
                    "candidate_fields": [],
                    "no_candidates": True,
                }
            )
            return context

        if field_name and field_name not in candidates:
            field_name = None
        if not field_name:
            field_name = candidates[0] if len(candidates) == 1 else None

        context.update(
            {
                "eval_config": eval_config,
                "queue": queue,
                "candidate_fields": candidates,
                "selected_field_name": field_name,
            }
        )
        if field_name is None:
            return context

        session_ct = ContentType.objects.get_for_model(ExperimentSession)

        judge_scores = list(
            Score.objects.filter(
                team=team,
                target_content_type=session_ct,
                name=field_name,
                source__in=[Score.Source.LLM_JUDGE, Score.Source.PROGRAMMATIC],
                automated_result__evaluator__in=eval_config.evaluators.all(),
            ).order_by("created_at", "id")
        )
        human_scores = list(
            Score.objects.filter(
                team=team,
                target_content_type=session_ct,
                name=field_name,
                source=Score.Source.HUMAN_REVIEW,
                review__item__queue=queue,
                review__is_authoritative=True,
            ).order_by("created_at", "id")
        )

        judge_by_target = _latest_score_per_target(judge_scores)
        human_by_target = _latest_score_per_target(human_scores)

        matched_targets = set(judge_by_target) & set(human_by_target)
        eval_only_targets = set(judge_by_target) - matched_targets
        human_only_targets = set(human_by_target) - matched_targets

        # Resolve session external IDs in one query
        all_target_ids = matched_targets | eval_only_targets | human_only_targets
        sessions_by_id = {s.id: s for s in ExperimentSession.objects.filter(id__in=all_target_ids)}

        rows: list[_Row] = []
        for target_id in sorted(matched_targets):
            j_val = _score_value(judge_by_target[target_id])
            h_val = _score_value(human_by_target[target_id])
            rows.append(
                _Row(
                    session_external_id=(
                        str(sessions_by_id[target_id].external_id) if target_id in sessions_by_id else None
                    ),
                    judge_value=j_val,
                    human_value=h_val,
                    agree=(j_val == h_val),
                )
            )

        agree_count = sum(1 for r in rows if r.agree)
        context.update(
            {
                "rows": rows,
                "matched_count": len(rows),
                "agree_count": agree_count,
                "eval_only_count": len(eval_only_targets),
                "human_only_count": len(human_only_targets),
            }
        )
        return context

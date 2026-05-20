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
from apps.human_annotations.models import AnnotationItem, AnnotationQueue
from apps.teams.mixins import LoginAndTeamRequiredMixin

_ROW_KINDS = ("matched", "eval_only", "human_only")
_SHOW_CHOICES = (*_ROW_KINDS, "all")
_DEFAULT_SHOW = "matched"


@dataclass
class _Row:
    kind: str
    session_external_id: str | None
    experiment_public_id: str | None
    judge_value: Any
    human_value: Any
    agree: bool | None
    eval_run_id: int | None
    annotation_item_id: int | None


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


def _session_fields(session: ExperimentSession | None) -> tuple[str | None, str | None]:
    """Return (external_id, experiment_public_id) for a session, or (None, None)."""
    if session is None:
        return None, None
    experiment = session.experiment
    return str(session.external_id), str(experiment.public_id) if experiment else None


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
        show = self.request.GET.get("show", _DEFAULT_SHOW)
        if show not in _SHOW_CHOICES:
            show = _DEFAULT_SHOW

        context: dict[str, Any] = {
            "active_tab": "evaluations",
            "page_title": "Concordance",
            "eval_configs": EvaluationConfig.objects.filter(team=team).order_by("name"),
            "queues": AnnotationQueue.objects.filter(team=team).order_by("name"),
            "selected_eval_id": eval_id,
            "selected_queue_id": queue_id,
            "selected_field_name": field_name,
            "selected_show": show,
            "show_choices": _SHOW_CHOICES,
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
            )
            .select_related("automated_result")
            .order_by("created_at", "id")
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

        # Resolve session external IDs + experiment public IDs in one query.
        all_target_ids = matched_targets | eval_only_targets | human_only_targets
        sessions_by_id = {
            s.id: s for s in ExperimentSession.objects.filter(id__in=all_target_ids).select_related("experiment")
        }
        # Resolve annotation item IDs (for the "Annotation" link) in one query.
        items_by_session = {
            item.session_id: item for item in AnnotationItem.objects.filter(queue=queue, session_id__in=all_target_ids)
        }

        def _eval_run_id(target_id: int) -> int | None:
            score = judge_by_target.get(target_id)
            return score.automated_result.run_id if score and score.automated_result_id else None

        def _annotation_item_id(target_id: int) -> int | None:
            item = items_by_session.get(target_id)
            return item.id if item else None

        rows: list[_Row] = []
        for target_id in sorted(matched_targets):
            j_val = _score_value(judge_by_target[target_id])
            h_val = _score_value(human_by_target[target_id])
            ext_id, exp_id = _session_fields(sessions_by_id.get(target_id))
            rows.append(
                _Row(
                    kind="matched",
                    session_external_id=ext_id,
                    experiment_public_id=exp_id,
                    judge_value=j_val,
                    human_value=h_val,
                    agree=(j_val == h_val),
                    eval_run_id=_eval_run_id(target_id),
                    annotation_item_id=_annotation_item_id(target_id),
                )
            )

        eval_only_rows = []
        for target_id in sorted(eval_only_targets):
            ext_id, exp_id = _session_fields(sessions_by_id.get(target_id))
            eval_only_rows.append(
                _Row(
                    kind="eval_only",
                    session_external_id=ext_id,
                    experiment_public_id=exp_id,
                    judge_value=_score_value(judge_by_target[target_id]),
                    human_value=None,
                    agree=None,
                    eval_run_id=_eval_run_id(target_id),
                    annotation_item_id=None,
                )
            )

        human_only_rows = []
        for target_id in sorted(human_only_targets):
            ext_id, exp_id = _session_fields(sessions_by_id.get(target_id))
            human_only_rows.append(
                _Row(
                    kind="human_only",
                    session_external_id=ext_id,
                    experiment_public_id=exp_id,
                    judge_value=None,
                    human_value=_score_value(human_by_target[target_id]),
                    agree=None,
                    eval_run_id=None,
                    annotation_item_id=_annotation_item_id(target_id),
                )
            )

        matched_count = len(rows)
        agree_count = sum(1 for r in rows if r.agree)
        agree_pct = round(agree_count / matched_count * 100) if matched_count else None

        if show == "matched":
            visible_rows = rows
        elif show == "eval_only":
            visible_rows = eval_only_rows
        elif show == "human_only":
            visible_rows = human_only_rows
        else:  # "all"
            visible_rows = rows + eval_only_rows + human_only_rows

        context.update(
            {
                "rows": visible_rows,
                "matched_count": matched_count,
                "agree_count": agree_count,
                "agree_pct": agree_pct,
                "eval_only_count": len(eval_only_rows),
                "human_only_count": len(human_only_rows),
            }
        )
        return context

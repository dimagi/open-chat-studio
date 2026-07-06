import csv
from dataclasses import dataclass
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from django.views.generic import TemplateView
from waffle import flag_is_active

from apps.assessments.models import Score
from apps.evaluations.models import EvaluationConfig, EvaluationMode
from apps.experiments.models import ExperimentSession
from apps.human_annotations.models import AnnotationItem, AnnotationQueue
from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin

_SESSION_MODE = EvaluationMode.SESSION

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
    eval_result_id: int | None
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


def _agreement_color_class(agree_pct: int | None) -> str:
    """Tailwind colour class for the headline agreement stat."""
    if agree_pct is None:
        return "text-base-content/40"
    if agree_pct >= 80:
        return "text-success"
    if agree_pct >= 50:
        return "text-warning"
    return "text-error"


def _session_fields(session: ExperimentSession | None) -> tuple[str | None, str | None]:
    """Return (external_id, experiment_public_id) for a session, or (None, None)."""
    if session is None:
        return None, None
    experiment = session.experiment
    return str(session.external_id), str(experiment.public_id) if experiment else None


def _resolve_field_name(field_name: str | None, candidates: list[str]) -> str | None:
    """Keep the query-provided field if valid; otherwise default to the sole candidate."""
    if field_name and field_name in candidates:
        return field_name
    return candidates[0] if len(candidates) == 1 else None


def _fetch_field_scores(
    *, team, eval_config: EvaluationConfig, queue: AnnotationQueue, field_name: str, session_ct: ContentType
) -> tuple[dict[int, Score], dict[int, Score]]:
    """Fetch judge + human Scores for one field, grouped by target session id."""
    judge_scores = list(
        Score.objects.filter(
            team=team,
            target_content_type=session_ct,
            name=field_name,
            source__in=[Score.Source.LLM_JUDGE, Score.Source.PROGRAMMATIC],
            # Filter by run.config so scores from other configs sharing the same
            # evaluator (Evaluator ↔ EvaluationConfig is M2M) are not pulled in.
            automated_result__run__config=eval_config,
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
    return _latest_score_per_target(judge_scores), _latest_score_per_target(human_scores)


def _make_row(
    kind: str,
    target_id: int,
    *,
    judge_by_target: dict[int, Score],
    human_by_target: dict[int, Score],
    sessions_by_id: dict[int, ExperimentSession],
    items_by_session: dict[int, AnnotationItem],
) -> _Row:
    """Build one concordance row. `kind` controls which side contributes values and links."""
    judge_score = judge_by_target.get(target_id) if kind != "human_only" else None
    human_score = human_by_target.get(target_id) if kind != "eval_only" else None
    # Only link to an annotation item when this session actually has human-side data.
    item = items_by_session.get(target_id) if kind != "eval_only" else None

    ext_id, exp_id = _session_fields(sessions_by_id.get(target_id))
    j_val = _score_value(judge_score) if judge_score is not None else None
    h_val = _score_value(human_score) if human_score is not None else None
    # NOTE: the eval results page's `?result_id=` is actually an EvaluationMessage.id
    # (its table rows are keyed by message — multiple evaluator results for the same
    # message merge into one row). Pass the message id, not the EvaluationResult.id,
    # so highlight + auto-paginate work.
    automated = judge_score.automated_result if judge_score and judge_score.automated_result_id else None

    return _Row(
        kind=kind,
        session_external_id=ext_id,
        experiment_public_id=exp_id,
        judge_value=j_val,
        human_value=h_val,
        agree=(j_val == h_val) if kind == "matched" else None,
        eval_run_id=automated.run_id if automated else None,
        eval_result_id=automated.message_id if automated else None,
        annotation_item_id=item.id if item else None,
    )


def _build_concordance_rows(
    *, team, eval_config: EvaluationConfig, queue: AnnotationQueue, field_name: str
) -> dict[str, list[_Row]]:
    """Compute rows for the concordance table, partitioned by kind."""
    session_ct = ContentType.objects.get_for_model(ExperimentSession)
    judge_by_target, human_by_target = _fetch_field_scores(
        team=team, eval_config=eval_config, queue=queue, field_name=field_name, session_ct=session_ct
    )

    matched = set(judge_by_target) & set(human_by_target)
    eval_only = set(judge_by_target) - matched
    human_only = set(human_by_target) - matched

    # Bulk-load session metadata and annotation items in one query each.
    all_target_ids = matched | eval_only | human_only
    sessions_by_id = {
        s.id: s for s in ExperimentSession.objects.filter(id__in=all_target_ids).select_related("experiment")
    }
    items_by_session = {
        item.session_id: item for item in AnnotationItem.objects.filter(queue=queue, session_id__in=all_target_ids)
    }

    return {
        kind: [
            _make_row(
                kind,
                target_id,
                judge_by_target=judge_by_target,
                human_by_target=human_by_target,
                sessions_by_id=sessions_by_id,
                items_by_session=items_by_session,
            )
            for target_id in sorted(target_ids)
        ]
        for kind, target_ids in (("matched", matched), ("eval_only", eval_only), ("human_only", human_only))
    }


class ConcordanceView(LoginAndTeamRequiredMixin, TemplateView):
    template_name = "assessments/concordance.html"

    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not (
            flag_is_active(request, "flag_assessments_concordance")
            and flag_is_active(request, "flag_evaluations")
            and flag_is_active(request, "flag_human_annotations")
        ):
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
            "eval_configs": EvaluationConfig.objects.filter(team=team, dataset__evaluation_mode=_SESSION_MODE).order_by(
                "name"
            ),
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
        context.update({"eval_config": eval_config, "queue": queue, "candidate_fields": candidates})

        if not candidates:
            context["no_candidates"] = True
            return context

        field_name = _resolve_field_name(field_name, candidates)
        context["selected_field_name"] = field_name
        if field_name is None:
            return context

        rows_by_kind = _build_concordance_rows(team=team, eval_config=eval_config, queue=queue, field_name=field_name)
        matched_rows = rows_by_kind["matched"]
        eval_only_rows = rows_by_kind["eval_only"]
        human_only_rows = rows_by_kind["human_only"]

        matched_count = len(matched_rows)
        agree_count = sum(1 for r in matched_rows if r.agree)
        agree_pct = round(agree_count / matched_count * 100) if matched_count else None

        rows_by_show = {
            "matched": matched_rows,
            "eval_only": eval_only_rows,
            "human_only": human_only_rows,
            "all": matched_rows + eval_only_rows + human_only_rows,
        }

        context.update(
            {
                "rows": rows_by_show[show],
                "matched_count": matched_count,
                "agree_count": agree_count,
                "agree_pct": agree_pct,
                "agreement_color_class": _agreement_color_class(agree_pct),
                "eval_only_count": len(eval_only_rows),
                "human_only_count": len(human_only_rows),
            }
        )
        return context


@login_and_team_required
def export_concordance_csv(request: HttpRequest, team_slug: str) -> HttpResponse:
    """Export concordance results as CSV.

    Accepts the same ``eval``, ``queue``, ``field``, and ``show`` query
    parameters as ``ConcordanceView``.  Returns a 404 when the feature flags
    are inactive or required parameters are missing.
    """
    if not (
        flag_is_active(request, "flag_assessments_concordance")
        and flag_is_active(request, "flag_evaluations")
        and flag_is_active(request, "flag_human_annotations")
    ):
        raise Http404("Concordance is not enabled for this team.")

    team = request.team
    eval_id = request.GET.get("eval")
    queue_id = request.GET.get("queue")
    field_name = request.GET.get("field")
    show = request.GET.get("show", "all")
    if show not in _SHOW_CHOICES:
        show = "all"

    if not eval_id or not queue_id:
        raise Http404("eval and queue parameters are required.")

    eval_config = get_object_or_404(EvaluationConfig, id=eval_id, team=team)
    queue = get_object_or_404(AnnotationQueue, id=queue_id, team=team)
    candidates = _candidate_categorical_fields(eval_config, queue)

    if not candidates:
        raise Http404("No matching categorical fields found.")

    field_name = _resolve_field_name(field_name, candidates)
    if field_name is None:
        raise Http404("field parameter is required when multiple fields exist.")

    rows_by_kind = _build_concordance_rows(team=team, eval_config=eval_config, queue=queue, field_name=field_name)
    rows_by_show = {
        "matched": rows_by_kind["matched"],
        "eval_only": rows_by_kind["eval_only"],
        "human_only": rows_by_kind["human_only"],
        "all": rows_by_kind["matched"] + rows_by_kind["eval_only"] + rows_by_kind["human_only"],
    }
    rows = rows_by_show[show]

    filename = f"concordance_{eval_config.name}_{queue.name}_{field_name}.csv".replace(" ", "_")
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow(
        [
            "kind",
            "session_external_id",
            "experiment_public_id",
            f"judge_{field_name}",
            f"human_{field_name}",
            "agree",
            "eval_run_id",
            "eval_result_id",
            "annotation_item_id",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row.kind,
                row.session_external_id,
                row.experiment_public_id,
                row.judge_value,
                row.human_value,
                row.agree,
                row.eval_run_id,
                row.eval_result_id,
                row.annotation_item_id,
            ]
        )

    return response

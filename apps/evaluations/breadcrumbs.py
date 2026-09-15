from django.urls import reverse
from django.utils.translation import gettext as _

from apps.evaluations.models import EvaluationConfig, EvaluationDataset, EvaluationRun
from apps.generics.breadcrumbs import Crumb


def datasets_crumbs(team_slug: str, dataset: EvaluationDataset | None = None) -> list[Crumb]:
    crumbs: list[Crumb] = [(_("Datasets"), reverse("evaluations:dataset_home", args=[team_slug]))]
    if dataset:
        crumbs.append((dataset.name, dataset.get_absolute_url()))
    return crumbs


def evaluations_crumbs(
    team_slug: str, config: EvaluationConfig | None = None, run: EvaluationRun | None = None
) -> list[Crumb]:
    crumbs: list[Crumb] = [(_("Evaluations"), reverse("evaluations:home", args=[team_slug]))]
    if config:
        crumbs.append((_("%(name)s Runs") % {"name": config.name}, config.get_absolute_url()))
    if run:
        crumbs.append((_("Run %(id)s") % {"id": run.id}, run.get_absolute_url()))
    return crumbs


def evaluators_crumbs(team_slug: str) -> list[Crumb]:
    return [(_("Evaluators"), reverse("evaluations:evaluator_home", args=[team_slug]))]

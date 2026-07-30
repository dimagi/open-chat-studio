from dataclasses import dataclass

from django.http import HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse

from apps.generics.chips import Chip
from apps.utils.deletion import is_bulk_archiveable


@dataclass
class ReferencedExperimentContext:
    """Experiment chips referencing an object, grouped by how the reference can be cleared.

    ``manual`` versions must be archived or unpublished by hand; ``bulk_archiveable``
    versions can be cleared together via the bulk-archive form in the modal.
    """

    manual: list[Chip]
    bulk_archiveable: list[Chip]
    bulk_archiveable_ids: list[int]
    bulk_archive_url: str

    def bulk_archive_kwargs(self) -> dict:
        """The bulk-archive keyword arguments for ``render_referenced_objects_modal``.

        These three are identical at every call site, so they can be spread with ``**``.
        The ``manual`` chips are passed separately because different call sites map them
        to different modal sections (``experiments`` vs ``experiments_with_pipeline_nodes``).
        """
        return {
            "bulk_archiveable_experiments": self.bulk_archiveable,
            "bulk_archiveable_ids": self.bulk_archiveable_ids,
            "bulk_archive_url": self.bulk_archive_url,
        }


def get_referenced_experiment_context(experiments, team_slug: str) -> ReferencedExperimentContext:
    """Split experiments referencing an object into manual vs bulk-archiveable chips.

    Every delete view that surfaces referencing experiments needs this same split, so
    the classification (and the ``is_bulk_archiveable`` check) happens here in a single
    pass rather than being duplicated at each call site.
    """
    manual: list[Chip] = []
    bulk_archiveable: list[Chip] = []
    bulk_archiveable_ids: list[int] = []
    for experiment in experiments:
        if is_bulk_archiveable(experiment):
            bulk_archiveable.append(
                Chip(label=f"{experiment.name} {experiment.get_version_name()}", url=experiment.get_absolute_url())
            )
            bulk_archiveable_ids.append(experiment.id)
        else:
            label = (
                f"{experiment.name} [{experiment.get_version_name()}]"
                if experiment.is_working_version
                else f"{experiment.name} {experiment.get_version_name()} [published]"
            )
            manual.append(Chip(label=label, url=experiment.get_absolute_url()))
    return ReferencedExperimentContext(
        manual=manual,
        bulk_archiveable=bulk_archiveable,
        bulk_archiveable_ids=bulk_archiveable_ids,
        bulk_archive_url=reverse("experiments:bulk_archive_versions", args=[team_slug]),
    )


def render_referenced_objects_modal(
    object_name: str,
    *,
    request=None,
    experiments: list | None = None,
    pipeline_nodes: list | None = None,
    experiments_with_pipeline_nodes: list | None = None,
    static_trigger_experiments: list | None = None,
    assistants: list | None = None,
    evaluators: list | None = None,
    bulk_archiveable_experiments: list | None = None,
    bulk_archiveable_ids: list | None = None,
    bulk_archive_url: str | None = None,
) -> HttpResponse:
    """Render a modal listing the objects still referencing ``object_name``.

    Returns an HTMX response that appends the modal to ``<body>`` so it pops
    over whatever page the delete was triggered from.

    ``request`` must be supplied when ``bulk_archive_url`` is set so the archive
    form can render a valid CSRF token.
    """
    html = render_to_string(
        "generic/referenced_objects_modal.html",
        request=request,
        context={
            "object_name": object_name,
            "experiments": experiments or [],
            "pipeline_nodes": pipeline_nodes or [],
            "experiments_with_pipeline_nodes": experiments_with_pipeline_nodes or [],
            "static_trigger_experiments": static_trigger_experiments or [],
            "assistants": assistants or [],
            "evaluators": evaluators or [],
            "bulk_archiveable_experiments": bulk_archiveable_experiments or [],
            "bulk_archiveable_ids": bulk_archiveable_ids or [],
            "bulk_archive_url": bulk_archive_url,
        },
    )
    response = HttpResponse(html)
    response["HX-Retarget"] = "body"
    response["HX-Reswap"] = "beforeend"
    return response

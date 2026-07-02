from django.http import HttpResponse
from django.template.loader import render_to_string


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

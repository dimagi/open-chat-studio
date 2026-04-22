from django.http import HttpResponse
from django.template.loader import render_to_string


def render_referenced_objects_modal(
    object_name: str,
    *,
    experiments: list | None = None,
    pipeline_nodes: list | None = None,
    experiments_with_pipeline_nodes: list | None = None,
    static_trigger_experiments: list | None = None,
    assistants: list | None = None,
) -> HttpResponse:
    """Render a modal listing the objects still referencing ``object_name``.

    Returns an HTMX response that appends the modal to ``<body>`` so it pops
    over whatever page the delete was triggered from.
    """
    html = render_to_string(
        "generic/referenced_objects_modal.html",
        context={
            "object_name": object_name,
            "experiments": experiments or [],
            "pipeline_nodes": pipeline_nodes or [],
            "experiments_with_pipeline_nodes": experiments_with_pipeline_nodes or [],
            "static_trigger_experiments": static_trigger_experiments or [],
            "assistants": assistants or [],
        },
    )
    response = HttpResponse(html)
    response["HX-Retarget"] = "body"
    response["HX-Reswap"] = "beforeend"
    return response

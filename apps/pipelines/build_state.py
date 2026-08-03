"""Build-state reporting for a pipeline: the errors report.

``Pipeline.validate()`` returns a complete :class:`~apps.pipelines.exceptions.ErrorReport`, which
this passes through unchanged::

    {"node": {<node_id>: {<field>: <message>}}, "edge": [<edge_id>], "pipeline": [<message>]}

``pipeline_valid`` is exactly "all three buckets empty" — nothing more.
"""

from apps.pipelines.exceptions import has_errors
from apps.pipelines.models import Pipeline


def pipeline_build_state(pipeline: Pipeline) -> dict:
    """``pipeline_valid`` + ``errors`` for a pipeline."""
    errors = pipeline.validate()
    return {
        "pipeline_valid": not has_errors(errors),
        "errors": errors,
    }

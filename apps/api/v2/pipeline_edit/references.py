"""Strict reference checking for node params (#4140, W6).

The one check that refuses rather than reports, because nothing downstream would ever tell the
caller. ``Node._sync_resource_fk_fields`` tests a referenced row against ``_base_manager`` with no
team filter, so another team's id is not coerced to null — it goes straight into the FK column, and
``Pipeline.validate`` says nothing about it.

Valid means the option list ``/pipeline/options/`` already offered for that param
(``reference_sources_for_type`` maps the one to the other), which is what keeps "what we offer" and
"what we accept" from drifting apart. Only the lists know the whole answer: ``SyntheticVoice`` has
no team column, ``LlmProviderModel.team`` is nullable for the global models, and ``custom_actions``,
``tools`` and ``collection_index_ids`` are team-scoped with no FK column at all.
"""

from typing import Any

from rest_framework.exceptions import ValidationError

from apps.api.v2.discovery.node_types import reference_param_names, reference_sources_for_type
from apps.api.v2.discovery.options import options_for_team
from apps.pipelines.nodes.base import BasePipelineNode
from apps.teams.models import Team

from .node_params import is_list_param

#: Values meaning "this reference is unset". ``[]`` matters as much as ``None``: a list-valued
#: param such as ``collection_index_ids`` clears by being sent empty.
UNSET = (None, [], "")


def option_lists_for(team: Team, params: dict[str, Any], node_class: type[BasePipelineNode] | None = None) -> dict:
    """``team``'s option lists, built only if ``params`` could name a resource at all.

    ``node_class`` is the exact answer and POST has it. A PATCH does not -- the node's type comes
    from the graph, under the lock -- so it settles for the union across every served type. Either
    way the set asked about here is a superset of the one :func:`_reference_errors` goes on to use,
    which is what makes an empty return safe: it can only happen when there is nothing to check.
    """
    names = reference_sources_for_type(node_class.__name__).keys() if node_class else reference_param_names()
    if not params or names.isdisjoint(params):
        return {}
    return options_for_team(team)


def check_references(options: dict, node_class: type[BasePipelineNode], params: dict[str, Any]) -> None:
    """Refuse ``params`` naming a resource the team cannot reach.

    Only the params actually sent are checked, so a PATCH never fails on a stale value it is not
    touching. A nonexistent id and another team's id are the same answer on purpose: separating them
    would report whether an id exists in some other team.
    """
    if errors := _reference_errors(options, node_class, params):
        raise ValidationError({"params": errors})


def _reference_errors(options: dict, node_class: type[BasePipelineNode], params: dict[str, Any]) -> dict[str, str]:
    """``param -> why its value names something out of reach``, for the params actually sent."""
    node_type = node_class.__name__
    sources = reference_sources_for_type(node_type)
    referencing = {param: value for param, value in params.items() if param in sources and value not in UNSET}
    errors: dict[str, str] = {}
    for param, value in referencing.items():
        source = sources[param]
        offered = options.get(source, [])
        if not isinstance(offered, list):
            # A dict-shaped source nests its options under provider types, so there is no flat set
            # to check a value against. The two that exist are excluded by name; this keeps one
            # added later from raising here rather than being skipped.
            continue
        allowed = {option["value"] for option in offered}
        supplied = value if is_list_param(node_class, param) else [value]
        unknown = [item for item in supplied if not _is_offered(item, allowed)]
        if unknown:
            errors[param] = (
                f"Not available to this team: {', '.join(repr(item) for item in unknown)}. "
                f"Choose from the '{source}' list in GET /api/v2/pipeline/options/{node_type}/."
            )
    return errors


def _is_offered(item: Any, allowed: set) -> bool:
    """Whether ``item`` is one of the values the team was offered.

    Nothing type-checks params before this runs, so a list can arrive where a scalar id belongs.
    Unhashable is no option either way, so it counts as unknown rather than raising.
    """
    try:
        return item in allowed
    except TypeError:
        return False

"""Strict reference checking for node params (#4140, W6).

Refuses rather than reports: nothing downstream would tell the caller. Another team's id lands in
the FK column untouched, and ``Pipeline.validate`` says nothing about it.

``PARAMETER_OPTION_SOURCES`` says which ``/pipeline/options/`` lists a team could be denied a value
from, and so which params are references. Each of those lists has a resolver that asks the database
what the team may actually use; they live in ``apps.pipelines.nodes.node_metadata``, beside the
querysets the option lists themselves are built from, so what a client is offered and what a write
accepts cannot come apart. The two are held to that by
``test_a_write_accepts_exactly_the_values_the_options_endpoint_offers``.

A resolver is reached through the source rather than the param -- ``node_metadata.get_resolver`` --
because the source is what decides the permitted values; the param name is only how the request body
spells it, and what an error has to be reported against. ``parameter_option_mapping`` carries a node
type's params over to the sources they draw from.
"""

from typing import Any

from rest_framework.exceptions import ValidationError

from apps.api.v2.discovery.node_types import parameter_option_mapping
from apps.pipelines.nodes.base import BasePipelineNode
from apps.pipelines.nodes.node_metadata import get_resolver
from apps.teams.models import Team

from .node_params import is_list_param

#: Values meaning "this reference is unset". ``[]`` matters as much as ``None``: a list-valued
#: param such as ``collection_index_ids`` clears by being sent empty.
UNSET = (None, [], "")


def check_references(team: Team, node_class: type[BasePipelineNode], params: dict[str, Any]) -> None:
    """Refuse ``params`` naming a resource the team cannot reach.

    Only the params actually sent are checked, so a PATCH never fails on a stale value it is not
    touching. A nonexistent id and another team's id are the same answer on purpose: separating them
    would report whether an id exists in some other team.
    """

    if errors := _reference_errors(team, node_class, params):
        raise ValidationError({"params": errors})


def _reference_errors(team: Team, node_class: type[BasePipelineNode], params: dict[str, Any]) -> dict[str, str]:
    """The params naming a resource the team cannot reach, keyed by param name, each with a message
    saying which option list to choose from instead.

    A param is only looked at if it is a reference and carries a value. ``None``, ``""`` and ``[]``
    unset a reference, so there is nothing to check.
    """
    node_type = node_class.__name__
    param_option_map = parameter_option_mapping(node_type)
    errors: dict[str, str] = {}
    for param, value in params.items():
        if param not in param_option_map or value in UNSET:
            continue
        # `is_list_param` reads the declaration, `isinstance` the value: `params` is untyped here,
        # so a bare `5` arrives where `[5]` belongs and iterating it would be a 500, not a 400.
        requested_values = (
            value if is_list_param(node_class, param) and isinstance(value, list | tuple | set) else [value]
        )
        # Via the source, not the param: the source is what says which records are on offer, and
        # `test_every_checked_param_has_a_resolver` is what says each one reached here has a
        # resolver rather than raising.
        available_values = get_resolver(param_option_map[param])(team, requested_values)
        if unknown := [item for item in requested_values if _is_not_allowed(item, available_values)]:
            errors[param] = (
                f"Not available to this team: {', '.join(repr(item) for item in unknown)}. "
                f"Choose from the '{param_option_map[param]}' list in GET /api/v2/pipeline/options/{node_type}/."
            )
    return errors


def _is_not_allowed(item: Any, allowed: set) -> bool:
    """Whether ``item`` is none of the values the team may use.

    Nothing type-checks params before this runs, so a list can arrive where a scalar id belongs. A
    resolver has already declined to return such a value -- none of them may raise on one -- so this
    only has to avoid raising on it in turn: unhashable is no resource either way, so it counts as
    unknown.
    """
    try:
        return item not in allowed
    except TypeError:
        return True

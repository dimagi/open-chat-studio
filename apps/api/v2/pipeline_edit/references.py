"""Strict reference checking for node params (#4140, W6).

Structural problems with a graph are reported and left to the agent to fix; a reference to
something the team does not hold is not, because nothing downstream would ever tell it. A bad
resource id is silently coerced to null by ``Node._sync_resource_fk_fields`` — which is not
team-scoped — so the write would look like it worked and the node would run without the resource.

What counts as valid is the option list ``/pipeline/options/`` already offered for that param
(``reference_sources_for_type`` maps the one to the other). Checking against the same lists the
discovery endpoint serves is what keeps "what we offer" and "what we accept" from drifting apart.
"""

from collections.abc import Callable
from typing import Any

from rest_framework.exceptions import ValidationError

from apps.api.v2.discovery.node_types import reference_sources_for_type
from apps.api.v2.discovery.options import options_for_team
from apps.teams.models import Team

from .param_types import is_array_param

#: A caller's lazy handle on the team's option lists, as :func:`team_options` builds it.
OptionsAccessor = Callable[[], dict]

#: Values meaning "this reference is unset". ``[]`` matters as much as ``None``: a list-valued
#: param such as ``collection_index_ids`` clears by being sent empty.
UNSET = (None, [], "")


def team_options(team: Team) -> OptionsAccessor:
    """A once-per-caller accessor for ``team``'s option lists.

    ``options_for_team`` is around fifteen queries and parses every custom action's OpenAPI schema,
    so it is built lazily (a write touching no reference never pays for it) and at most once.
    """
    built: list[dict] = []

    def get() -> dict:
        if not built:
            built.append(options_for_team(team))
        return built[0]

    return get


def check_references(options: OptionsAccessor, node_type: str, properties: dict, params: dict[str, Any]) -> None:
    """Refuse ``params`` naming a resource the team cannot reference.

    ``options`` is the accessor from ``team_options``, called only if there is something to check.

    Only the params actually sent are checked, so a PATCH never fails on a stale value it is not
    touching. A nonexistent id and another team's id are the same answer on purpose: separating
    them would report whether an id exists in some other team.
    """
    if errors := reference_errors(options, node_type, properties, params):
        raise ValidationError({"params": errors})


def reference_errors(
    options: OptionsAccessor, node_type: str, properties: dict, params: dict[str, Any]
) -> dict[str, str]:
    """``param -> why its value names something out of reach``, for the params actually sent."""
    sources = reference_sources_for_type(node_type)
    referencing = {param: value for param, value in params.items() if param in sources and value not in UNSET}
    if not referencing:
        # No reference to check, so don't pay for the team's option lists.
        return {}

    available = options()
    errors: dict[str, str] = {}
    for param, value in referencing.items():
        source = sources[param]
        offered = available.get(source, [])
        if not isinstance(offered, list):
            # A dict-shaped source nests its options under provider types, so there is no flat set
            # to check a value against. The two that exist are excluded by name; this keeps one
            # added later from raising here rather than being skipped.
            continue
        allowed = {option["value"] for option in offered}
        # Whether to look inside the value comes from the declared type, not from the value's own
        # shape: reading a one-element array as a list of references is exactly how a scalar id
        # wrapped in `[...]` used to pass this check and then break the node on read.
        supplied = value if is_array_param(properties, param) else [value]
        unknown = [item for item in supplied if item not in allowed]
        if unknown:
            errors[param] = (
                f"Not available to this team: {', '.join(repr(item) for item in unknown)}. "
                f"Choose from the '{source}' list in GET /api/v2/pipeline/options/{node_type}/."
            )
    return errors

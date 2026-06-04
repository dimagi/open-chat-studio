"""Small pure helpers shared across the v2 API. No Django/model imports — safe to import anywhere
(including low-level modules), so it carries no circular-import risk."""


def as_int(value) -> int | None:
    """Coerce a (possibly malformed) param id to ``int``, or ``None`` if it can't be."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_custom_actions(value) -> list[tuple[int, list[str]]]:
    """``custom_actions`` values are ``"{action_id}:{operation_id}"`` strings. Group the selected
    operation ids per custom action, preserving first-seen order."""
    selections: dict[int, list[str]] = {}
    for entry in value or []:
        action_part, _, operation_id = str(entry).partition(":")
        action_id = as_int(action_part)
        if action_id is None:
            continue
        operation_ids = selections.setdefault(action_id, [])
        if operation_id and operation_id not in operation_ids:
            operation_ids.append(operation_id)
    return list(selections.items())

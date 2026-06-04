def as_int(value) -> int | None:
    """Convert a value to an int, returning None if it can't be (e.g. a malformed id from JSON)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_custom_actions(value) -> list[tuple[int, list[str]]]:
    """Group selected operation ids by custom action.

    Each ``custom_actions`` entry is an ``"{action_id}:{operation_id}"`` string. Returns one entry
    per action, with its operation ids in the order they were first seen.
    """
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
